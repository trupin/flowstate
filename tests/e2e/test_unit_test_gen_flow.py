"""Integration test for the unit test generation flow.

Tests a complex flow with conditional routing, retry loops, and multi-path
convergence using a mock subprocess manager. No real Claude Code processes
are launched. The flow uses judge=false (self-report mode), so the mock
writes DECISION.json files to drive routing decisions.

Test cases:
1. Happy path: no defects, PR passes first time
2. Defect fix path: defects found, developer fixes, PR passes
3. PR retry once: PR fails, auto-remediate succeeds on attempt 1
4. PR retry escalation: PR fails 3 times, escalate to ADAPT
5. Defect escalate path: defects found, escalate to owner, then continue
6. Defect skip path: defects found, developer skips, then continue
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from flowstate.dsl.parser import parse_flow
from flowstate.dsl.type_checker import check_flow
from flowstate.engine.executor import FlowExecutor
from flowstate.engine.subprocess_mgr import StreamEvent, StreamEventType
from flowstate.state.repository import FlowstateDB

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from flowstate.engine.events import FlowEvent


FLOW_PATH = Path(__file__).parent / "flows" / "unit_test_gen.flow"


# ---------------------------------------------------------------------------
# Routing mock subprocess manager
# ---------------------------------------------------------------------------


class RoutingMockSubprocessManager:
    """Mock satisfying the Harness protocol that writes DECISION.json for self-report routing.

    For each conditional node, the mock writes a DECISION.json to the task_dir
    with the routing decision. The task_dir is extracted from the prompt text
    where the engine includes the "Write coordination files to <path>/" line.
    """

    def __init__(self, decisions: dict[str, str]) -> None:
        """Initialize with routing decisions.

        Args:
            decisions: Maps node_name to target_node_name for conditional routing.
        """
        self._decisions = decisions
        self.executed_nodes: list[str] = []

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Simulate running a task subprocess.

        Extracts the node name and task_dir from the prompt, writes DECISION.json
        if this node has a routing decision, writes SUMMARY.md, then yields
        standard success events.
        """
        node_name = self._extract_node_name(prompt)
        task_dir = self._extract_task_dir(prompt)
        self.executed_nodes.append(node_name)

        if task_dir:
            task_dir_path = Path(task_dir)
            task_dir_path.mkdir(parents=True, exist_ok=True)

            # Write DECISION.json if this node has a routing decision
            if node_name in self._decisions:
                decision_data = {
                    "decision": self._decisions[node_name],
                    "reasoning": f"Mock routing for {node_name}",
                    "confidence": 0.95,
                }
                (task_dir_path / "DECISION.json").write_text(json.dumps(decision_data))

            # Write SUMMARY.md
            (task_dir_path / "SUMMARY.md").write_text(f"Completed {node_name}")

        # Yield standard success events
        yield StreamEvent(
            type=StreamEventType.ASSISTANT,
            content={
                "type": "assistant",
                "message": {"content": [{"text": f"Working on {node_name}..."}]},
            },
            raw=json.dumps({"type": "assistant"}),
        )
        yield StreamEvent(
            type=StreamEventType.RESULT,
            content={"type": "result", "result": "Done.", "duration_ms": 100, "cost_usd": 0.01},
            raw=json.dumps({"type": "result", "result": "Done."}),
        )
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        async for event in self.run_task(prompt, workspace, resume_session_id):
            yield event

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> None:
        raise NotImplementedError("Judge not mocked for routing tests")

    async def kill(self, session_id: str) -> None:
        pass

    async def start_session(self, workspace: str, session_id: str) -> None:
        pass

    async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]:
        async for event in self.run_task(message, ".", session_id):
            yield event

    async def interrupt(self, session_id: str) -> None:
        pass

    @staticmethod
    def _extract_node_name(prompt: str) -> str:
        """Extract node name from the [flowstate:node=NAME] marker in the prompt."""
        for line in prompt.splitlines():
            stripped = line.strip()
            if stripped.startswith("[flowstate:node=") and stripped.endswith("]"):
                return stripped[len("[flowstate:node=") : -1]
        return "unknown"

    @staticmethod
    def _extract_task_dir(prompt: str) -> str | None:
        """Extract the task directory path from the prompt.

        The engine includes a line like:
            Write coordination files to /path/tasks/node-gen/.
        """
        m = re.search(r"Write coordination files to (.+)/\.\s*$", prompt, re.MULTILINE)
        if m:
            return m.group(1)
        # Fallback: look for SUMMARY.md path
        m = re.search(r"SUMMARY\.md to (.+)/SUMMARY\.md", prompt)
        if m:
            return m.group(1)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_flow():
    """Parse and return the unit_test_gen flow."""
    source = FLOW_PATH.read_text()
    return parse_flow(source)


def _make_db() -> FlowstateDB:
    """Create an in-memory SQLite database."""
    return FlowstateDB(":memory:")


def _collect_events() -> tuple[list[FlowEvent], object]:
    """Create an event collector callback."""
    events: list[FlowEvent] = []
    return events, events.append


def _get_executed_node_names(db: FlowstateDB, flow_run_id: str) -> list[str]:
    """Get the ordered list of executed node names from the DB."""
    tasks = db.list_task_executions(flow_run_id)
    # Sort by created_at or natural order (tasks are created in execution order)
    return [t.node_name for t in tasks]


async def _run_flow(
    decisions: dict[str, str],
    params: dict[str, str | float | bool] | None = None,
) -> tuple[str, FlowstateDB, RoutingMockSubprocessManager, list[FlowEvent]]:
    """Run the unit_test_gen flow with the given routing decisions.

    Returns (flow_run_id, db, mock_mgr, events).
    """
    flow = _load_flow()
    db = _make_db()
    events, callback = _collect_events()
    mock_mgr = RoutingMockSubprocessManager(decisions)
    executor = FlowExecutor(db, callback, mock_mgr, worktree_cleanup=False)

    flow_run_id = await executor.execute(
        flow,
        params or {"ticket_id": "JIRA-123", "repo": "test-repo"},
        "/tmp/test-workspace",
    )
    return flow_run_id, db, mock_mgr, events


# ---------------------------------------------------------------------------
# Parse and type check
# ---------------------------------------------------------------------------


class TestFlowDSL:
    """Verify the flow DSL file parses and type-checks successfully."""

    def test_flow_parses(self) -> None:
        source = FLOW_PATH.read_text()
        flow = parse_flow(source)
        assert flow.name == "unit_test_gen"
        assert len(flow.nodes) == 15
        assert len(flow.edges) == 21

    def test_flow_typechecks(self) -> None:
        source = FLOW_PATH.read_text()
        flow = parse_flow(source)
        errors = check_flow(flow)
        assert len(errors) == 0, f"Type check errors: {errors}"

    def test_judge_is_false(self) -> None:
        flow = _load_flow()
        assert flow.judge is False

    def test_all_nodes_present(self) -> None:
        flow = _load_flow()
        expected_nodes = {
            "receive_ticket",
            "analyze_code",
            "developer_decision",
            "fix_defects",
            "skip_and_continue",
            "escalate_defects",
            "generate_tests",
            "open_pr",
            "auto_remediate_1",
            "auto_remediate_2",
            "auto_remediate_3",
            "escalate_to_adapt",
            "pr_ready",
            "approve_and_merge",
            "ticket_closed",
        }
        assert set(flow.nodes.keys()) == expected_nodes

    def test_input_fields(self) -> None:
        flow = _load_flow()
        assert len(flow.input_fields) == 2
        field_names = {f.name for f in flow.input_fields}
        assert field_names == {"ticket_id", "repo"}


# ---------------------------------------------------------------------------
# Integration tests: full flow execution
# ---------------------------------------------------------------------------


class TestHappyPath:
    """No defects found, generate tests, PR passes, ticket closed."""

    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        decisions = {
            "analyze_code": "generate_tests",  # "no defects found"
            "open_pr": "pr_ready",  # "checks pass"
        }
        flow_run_id, db, _mock_mgr, _events = await _run_flow(decisions)

        # Flow should be completed
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        # Verify the execution path
        expected_path = [
            "receive_ticket",
            "analyze_code",
            "generate_tests",
            "open_pr",
            "pr_ready",
            "approve_and_merge",
            "ticket_closed",
        ]
        executed = _get_executed_node_names(db, flow_run_id)
        assert executed == expected_path

    @pytest.mark.asyncio
    async def test_happy_path_all_tasks_completed(self) -> None:
        decisions = {
            "analyze_code": "generate_tests",
            "open_pr": "pr_ready",
        }
        flow_run_id, db, _mock, _events = await _run_flow(decisions)

        tasks = db.list_task_executions(flow_run_id)
        for task in tasks:
            assert task.status == "completed", f"Task {task.node_name} has status {task.status}"


class TestDefectFixPath:
    """Defects found, developer fixes, generate tests, PR passes."""

    @pytest.mark.asyncio
    async def test_defect_fix_path(self) -> None:
        decisions = {
            "analyze_code": "developer_decision",  # "defects found"
            "developer_decision": "fix_defects",  # "fix first"
            "open_pr": "pr_ready",  # "checks pass"
        }
        flow_run_id, db, _mock_mgr, _events = await _run_flow(decisions)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        expected_path = [
            "receive_ticket",
            "analyze_code",
            "developer_decision",
            "fix_defects",
            "generate_tests",
            "open_pr",
            "pr_ready",
            "approve_and_merge",
            "ticket_closed",
        ]
        executed = _get_executed_node_names(db, flow_run_id)
        assert executed == expected_path


class TestDefectSkipPath:
    """Defects found, developer skips, generate tests, PR passes."""

    @pytest.mark.asyncio
    async def test_defect_skip_path(self) -> None:
        decisions = {
            "analyze_code": "developer_decision",  # "defects found"
            "developer_decision": "skip_and_continue",  # "skip fixes"
            "open_pr": "pr_ready",  # "checks pass"
        }
        flow_run_id, db, _mock_mgr, _events = await _run_flow(decisions)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        expected_path = [
            "receive_ticket",
            "analyze_code",
            "developer_decision",
            "skip_and_continue",
            "generate_tests",
            "open_pr",
            "pr_ready",
            "approve_and_merge",
            "ticket_closed",
        ]
        executed = _get_executed_node_names(db, flow_run_id)
        assert executed == expected_path


class TestDefectEscalatePath:
    """Defects found, escalate to code owner, generate tests, PR passes."""

    @pytest.mark.asyncio
    async def test_defect_escalate(self) -> None:
        decisions = {
            "analyze_code": "developer_decision",  # "defects found"
            "developer_decision": "escalate_defects",  # "escalate"
            "open_pr": "pr_ready",  # "checks pass"
        }
        flow_run_id, db, _mock, _events = await _run_flow(decisions)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        expected_path = [
            "receive_ticket",
            "analyze_code",
            "developer_decision",
            "escalate_defects",
            "generate_tests",
            "open_pr",
            "pr_ready",
            "approve_and_merge",
            "ticket_closed",
        ]
        executed = _get_executed_node_names(db, flow_run_id)
        assert executed == expected_path


class TestPrRetryOnce:
    """PR fails, auto-remediate attempt 1 succeeds, PR ready."""

    @pytest.mark.asyncio
    async def test_pr_retry_once(self) -> None:
        decisions = {
            "analyze_code": "generate_tests",  # "no defects found"
            "open_pr": "auto_remediate_1",  # "checks fail"
            "auto_remediate_1": "pr_ready",  # "fixed"
        }
        flow_run_id, db, _mock_mgr, _events = await _run_flow(decisions)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        expected_path = [
            "receive_ticket",
            "analyze_code",
            "generate_tests",
            "open_pr",
            "auto_remediate_1",
            "pr_ready",
            "approve_and_merge",
            "ticket_closed",
        ]
        executed = _get_executed_node_names(db, flow_run_id)
        assert executed == expected_path


class TestPrRetryTwice:
    """PR fails, auto-remediate 1 fails, attempt 2 succeeds."""

    @pytest.mark.asyncio
    async def test_pr_retry_twice(self) -> None:
        decisions = {
            "analyze_code": "generate_tests",
            "open_pr": "auto_remediate_1",  # "checks fail"
            "auto_remediate_1": "auto_remediate_2",  # "not fixed"
            "auto_remediate_2": "pr_ready",  # "fixed"
        }
        flow_run_id, db, _mock, _events = await _run_flow(decisions)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        expected_path = [
            "receive_ticket",
            "analyze_code",
            "generate_tests",
            "open_pr",
            "auto_remediate_1",
            "auto_remediate_2",
            "pr_ready",
            "approve_and_merge",
            "ticket_closed",
        ]
        executed = _get_executed_node_names(db, flow_run_id)
        assert executed == expected_path


class TestPrRetryEscalation:
    """PR fails 3 times, escalate to ADAPT."""

    @pytest.mark.asyncio
    async def test_pr_retry_escalation(self) -> None:
        decisions = {
            "analyze_code": "generate_tests",
            "open_pr": "auto_remediate_1",  # "checks fail"
            "auto_remediate_1": "auto_remediate_2",  # "not fixed"
            "auto_remediate_2": "auto_remediate_3",  # "not fixed"
            "auto_remediate_3": "escalate_to_adapt",  # "not fixed"
        }
        flow_run_id, db, _mock_mgr, _events = await _run_flow(decisions)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        expected_path = [
            "receive_ticket",
            "analyze_code",
            "generate_tests",
            "open_pr",
            "auto_remediate_1",
            "auto_remediate_2",
            "auto_remediate_3",
            "escalate_to_adapt",
            "approve_and_merge",
            "ticket_closed",
        ]
        executed = _get_executed_node_names(db, flow_run_id)
        assert executed == expected_path


class TestDefectFixWithPrRetry:
    """Defects found, developer fixes, PR fails once then passes."""

    @pytest.mark.asyncio
    async def test_defect_fix_with_pr_retry(self) -> None:
        decisions = {
            "analyze_code": "developer_decision",  # "defects found"
            "developer_decision": "fix_defects",  # "fix first"
            "open_pr": "auto_remediate_1",  # "checks fail"
            "auto_remediate_1": "pr_ready",  # "fixed"
        }
        flow_run_id, db, _mock, _events = await _run_flow(decisions)

        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

        expected_path = [
            "receive_ticket",
            "analyze_code",
            "developer_decision",
            "fix_defects",
            "generate_tests",
            "open_pr",
            "auto_remediate_1",
            "pr_ready",
            "approve_and_merge",
            "ticket_closed",
        ]
        executed = _get_executed_node_names(db, flow_run_id)
        assert executed == expected_path


class TestAllPathsReachExit:
    """Verify all test paths reach the exit node (ticket_closed)."""

    @pytest.mark.asyncio
    async def test_all_paths_reach_exit(self) -> None:
        """Run each major path and verify ticket_closed is always reached."""
        path_configs = [
            # Happy path
            {"analyze_code": "generate_tests", "open_pr": "pr_ready"},
            # Defect fix path
            {
                "analyze_code": "developer_decision",
                "developer_decision": "fix_defects",
                "open_pr": "pr_ready",
            },
            # Defect skip path
            {
                "analyze_code": "developer_decision",
                "developer_decision": "skip_and_continue",
                "open_pr": "pr_ready",
            },
            # Defect escalate path
            {
                "analyze_code": "developer_decision",
                "developer_decision": "escalate_defects",
                "open_pr": "pr_ready",
            },
            # PR retry escalation
            {
                "analyze_code": "generate_tests",
                "open_pr": "auto_remediate_1",
                "auto_remediate_1": "auto_remediate_2",
                "auto_remediate_2": "auto_remediate_3",
                "auto_remediate_3": "escalate_to_adapt",
            },
        ]
        for i, decisions in enumerate(path_configs):
            flow_run_id, db, _mock_mgr, _events = await _run_flow(decisions)
            run = db.get_flow_run(flow_run_id)
            assert run is not None, f"Path {i}: run not found"
            assert run.status == "completed", f"Path {i}: status is {run.status}"
            executed = _get_executed_node_names(db, flow_run_id)
            assert (
                executed[-1] == "ticket_closed"
            ), f"Path {i}: last node is {executed[-1]}, expected ticket_closed"
