"""Tests for FlowExecutor orchestrator integration (ENGINE-015).

Verifies that FlowExecutor can route task execution through an OrchestratorManager
instead of directly spawning Claude Code subprocesses. Also verifies fallback
behavior when the orchestrator path fails.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from flowstate.dsl.ast import (
    ContextMode,
    Edge,
    EdgeType,
    ErrorPolicy,
    Flow,
    Node,
    NodeType,
)
from flowstate.engine.executor import FlowExecutor
from flowstate.engine.orchestrator import (
    OrchestratorManager,
    OrchestratorSession,
    build_task_instruction,
)
from flowstate.engine.subprocess_mgr import StreamEvent, StreamEventType, SubprocessManager
from flowstate.state.repository import FlowstateDB

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from flowstate.engine.events import FlowEvent


# ---------------------------------------------------------------------------
# Mock subprocess manager (same pattern as test_executor.py)
# ---------------------------------------------------------------------------


class MockSubprocessManager(SubprocessManager):
    """A test double that returns configurable StreamEvent sequences."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str, str]] = []
        self.resume_calls: list[tuple[str, str, str]] = []
        self.kill_calls: list[str] = []

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.calls.append((prompt, workspace, session_id))
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
        self.resume_calls.append((prompt, workspace, resume_session_id))
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def kill(self, session_id: str) -> None:
        self.kill_calls.append(session_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_linear_flow(
    workspace: str = "/workspace",
) -> Flow:
    """Build a 3-node linear flow: start -> work -> finish."""
    nodes = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Do the start step"),
        "work": Node(name="work", node_type=NodeType.TASK, prompt="Do the work step"),
        "finish": Node(name="finish", node_type=NodeType.EXIT, prompt="Do the finish step"),
    }
    edges = (
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="start",
            target="work",
        ),
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="work",
            target="finish",
        ),
    )
    return Flow(
        name="test-flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=edges,
    )


def _collect_events() -> tuple[list[FlowEvent], object]:
    """Create an event collector callback and list."""
    events: list[FlowEvent] = []

    def callback(event: FlowEvent) -> None:
        events.append(event)

    return events, callback


# ---------------------------------------------------------------------------
# Tests: build_task_instruction
# ---------------------------------------------------------------------------


class TestBuildTaskInstruction:
    def test_contains_node_name(self) -> None:
        result = build_task_instruction(
            node_name="work",
            generation=1,
            input_path="/data/tasks/work-1/INPUT.md",
            task_dir="/data/tasks/work-1",
            cwd="/project",
        )
        assert '"work"' in result

    def test_contains_generation(self) -> None:
        result = build_task_instruction(
            node_name="work",
            generation=3,
            input_path="/data/tasks/work-3/INPUT.md",
            task_dir="/data/tasks/work-3",
            cwd="/project",
        )
        assert "generation 3" in result

    def test_contains_input_path(self) -> None:
        result = build_task_instruction(
            node_name="work",
            generation=1,
            input_path="/data/tasks/work-1/INPUT.md",
            task_dir="/data/tasks/work-1",
            cwd="/project",
        )
        assert "/data/tasks/work-1/INPUT.md" in result

    def test_contains_task_dir_summary_path(self) -> None:
        result = build_task_instruction(
            node_name="work",
            generation=1,
            input_path="/data/tasks/work-1/INPUT.md",
            task_dir="/data/tasks/work-1",
            cwd="/project",
        )
        assert "/data/tasks/work-1/SUMMARY.md" in result

    def test_contains_cwd(self) -> None:
        result = build_task_instruction(
            node_name="work",
            generation=1,
            input_path="/data/tasks/work-1/INPUT.md",
            task_dir="/data/tasks/work-1",
            cwd="/project",
        )
        assert "/project" in result

    def test_contains_agent_model(self) -> None:
        result = build_task_instruction(
            node_name="work",
            generation=1,
            input_path="/data/tasks/work-1/INPUT.md",
            task_dir="/data/tasks/work-1",
            cwd="/project",
        )
        assert '"opus"' in result


# ---------------------------------------------------------------------------
# Tests: executor with orchestrator
# ---------------------------------------------------------------------------


class TestExecuteWithOrchestrator:
    @pytest.mark.asyncio
    async def test_orchestrator_path_resumes_session(self, tmp_path: Path) -> None:
        """When OrchestratorManager is provided, tasks are routed through it."""
        db = FlowstateDB(":memory:")
        _events, callback = _collect_events()
        mock_sub = MockSubprocessManager()
        flow = _make_linear_flow(workspace=str(tmp_path))

        # Create a mock OrchestratorManager
        orch_mgr = AsyncMock(spec=OrchestratorManager)
        orch_session = OrchestratorSession(
            session_id="orch-session-123",
            harness="claude",
            cwd=str(tmp_path),
            data_dir=str(tmp_path / "orch"),
            is_initialized=True,
        )
        orch_mgr.get_or_create = AsyncMock(return_value=orch_session)
        orch_mgr.terminate_all = AsyncMock()

        executor = FlowExecutor(
            db=db,
            event_callback=callback,
            subprocess_mgr=mock_sub,
            orchestrator_mgr=orch_mgr,
        )

        flow_run_id = await executor.execute(flow, {}, str(tmp_path))

        # Verify orchestrator was used: get_or_create should have been called
        # (once for each of the 3 tasks: start, work, finish)
        assert orch_mgr.get_or_create.call_count >= 1

        # Verify subprocess_mgr.run_task_resume was called (orchestrator path)
        assert len(mock_sub.resume_calls) >= 1

        # Verify INPUT.md was written for at least one task
        task_executions = db.list_task_executions(flow_run_id)
        input_files_found = 0
        for te in task_executions:
            input_path = Path(te.task_dir) / "INPUT.md"
            if input_path.exists():
                input_files_found += 1
        assert input_files_found >= 1

        # Flow should complete successfully
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    @pytest.mark.asyncio
    async def test_orchestrator_resume_uses_session_id(self, tmp_path: Path) -> None:
        """The orchestrator session_id is passed to run_task_resume."""
        db = FlowstateDB(":memory:")
        _events, callback = _collect_events()
        mock_sub = MockSubprocessManager()
        flow = _make_linear_flow(workspace=str(tmp_path))

        orch_mgr = AsyncMock(spec=OrchestratorManager)
        orch_session = OrchestratorSession(
            session_id="my-orch-session",
            harness="claude",
            cwd=str(tmp_path),
            data_dir=str(tmp_path / "orch"),
            is_initialized=True,
        )
        orch_mgr.get_or_create = AsyncMock(return_value=orch_session)
        orch_mgr.terminate_all = AsyncMock()

        executor = FlowExecutor(
            db=db,
            event_callback=callback,
            subprocess_mgr=mock_sub,
            orchestrator_mgr=orch_mgr,
        )

        await executor.execute(flow, {}, str(tmp_path))

        # All resume calls should use the orchestrator session_id
        for _prompt, _workspace, sid in mock_sub.resume_calls:
            assert sid == "my-orch-session"

    @pytest.mark.asyncio
    async def test_orchestrator_instruction_contains_task_info(self, tmp_path: Path) -> None:
        """The instruction sent to the orchestrator contains task-relevant info."""
        db = FlowstateDB(":memory:")
        _events, callback = _collect_events()
        mock_sub = MockSubprocessManager()
        flow = _make_linear_flow(workspace=str(tmp_path))

        orch_mgr = AsyncMock(spec=OrchestratorManager)
        orch_session = OrchestratorSession(
            session_id="orch-123",
            harness="claude",
            cwd=str(tmp_path),
            data_dir=str(tmp_path / "orch"),
            is_initialized=True,
        )
        orch_mgr.get_or_create = AsyncMock(return_value=orch_session)
        orch_mgr.terminate_all = AsyncMock()

        executor = FlowExecutor(
            db=db,
            event_callback=callback,
            subprocess_mgr=mock_sub,
            orchestrator_mgr=orch_mgr,
        )

        await executor.execute(flow, {}, str(tmp_path))

        # Check that resume calls contain task instructions (not full prompts)
        for prompt, _workspace, _sid in mock_sub.resume_calls:
            # Orchestrator instructions reference INPUT.md, not full prompt text
            assert "INPUT.md" in prompt
            assert "SUMMARY.md" in prompt

    @pytest.mark.asyncio
    async def test_terminate_all_called_on_completion(self, tmp_path: Path) -> None:
        """terminate_all is called when flow completes."""
        db = FlowstateDB(":memory:")
        _events, callback = _collect_events()
        mock_sub = MockSubprocessManager()
        flow = _make_linear_flow(workspace=str(tmp_path))

        orch_mgr = AsyncMock(spec=OrchestratorManager)
        orch_session = OrchestratorSession(
            session_id="orch-123",
            harness="claude",
            cwd=str(tmp_path),
            data_dir=str(tmp_path / "orch"),
            is_initialized=True,
        )
        orch_mgr.get_or_create = AsyncMock(return_value=orch_session)
        orch_mgr.terminate_all = AsyncMock()

        executor = FlowExecutor(
            db=db,
            event_callback=callback,
            subprocess_mgr=mock_sub,
            orchestrator_mgr=orch_mgr,
        )

        flow_run_id = await executor.execute(flow, {}, str(tmp_path))

        # Give the fire-and-forget task a chance to run
        await asyncio.sleep(0.05)

        # terminate_all should have been called
        orch_mgr.terminate_all.assert_called_once_with(flow_run_id)


# ---------------------------------------------------------------------------
# Tests: executor without orchestrator (backward compatibility)
# ---------------------------------------------------------------------------


class TestExecuteWithoutOrchestrator:
    @pytest.mark.asyncio
    async def test_direct_subprocess_path(self, tmp_path: Path) -> None:
        """Without OrchestratorManager, tasks go through direct subprocess."""
        db = FlowstateDB(":memory:")
        _events, callback = _collect_events()
        mock_sub = MockSubprocessManager()
        flow = _make_linear_flow(workspace=str(tmp_path))

        executor = FlowExecutor(
            db=db,
            event_callback=callback,
            subprocess_mgr=mock_sub,
        )

        flow_run_id = await executor.execute(flow, {}, str(tmp_path))

        # Should use run_task directly (not run_task_resume)
        assert len(mock_sub.calls) >= 1
        # No resume calls (no orchestrator)
        assert len(mock_sub.resume_calls) == 0

        # Flow should complete
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    @pytest.mark.asyncio
    async def test_no_input_md_without_orchestrator(self, tmp_path: Path) -> None:
        """Without orchestrator, INPUT.md is NOT written."""
        db = FlowstateDB(":memory:")
        _events, callback = _collect_events()
        mock_sub = MockSubprocessManager()
        flow = _make_linear_flow(workspace=str(tmp_path))

        executor = FlowExecutor(
            db=db,
            event_callback=callback,
            subprocess_mgr=mock_sub,
        )

        flow_run_id = await executor.execute(flow, {}, str(tmp_path))

        # No INPUT.md should exist (direct subprocess receives prompt inline)
        task_executions = db.list_task_executions(flow_run_id)
        for te in task_executions:
            input_path = Path(te.task_dir) / "INPUT.md"
            assert not input_path.exists()


# ---------------------------------------------------------------------------
# Tests: orchestrator fallback
# ---------------------------------------------------------------------------


class TestOrchestratorFallback:
    @pytest.mark.asyncio
    async def test_fallback_on_get_or_create_failure(self, tmp_path: Path) -> None:
        """If orchestrator.get_or_create raises, fall back to direct subprocess."""
        db = FlowstateDB(":memory:")
        _events, callback = _collect_events()
        mock_sub = MockSubprocessManager()
        flow = _make_linear_flow(workspace=str(tmp_path))

        orch_mgr = AsyncMock(spec=OrchestratorManager)
        orch_mgr.get_or_create = AsyncMock(side_effect=RuntimeError("Session init failed"))
        orch_mgr.terminate_all = AsyncMock()

        executor = FlowExecutor(
            db=db,
            event_callback=callback,
            subprocess_mgr=mock_sub,
            orchestrator_mgr=orch_mgr,
        )

        flow_run_id = await executor.execute(flow, {}, str(tmp_path))

        # Should have fallen back to direct subprocess
        assert len(mock_sub.calls) >= 1

        # Flow should still complete
        run = db.get_flow_run(flow_run_id)
        assert run is not None
        assert run.status == "completed"

    @pytest.mark.asyncio
    async def test_fallback_disables_orchestrator_for_run(self, tmp_path: Path) -> None:
        """After fallback, orchestrator is disabled for all remaining tasks."""
        db = FlowstateDB(":memory:")
        _events, callback = _collect_events()
        mock_sub = MockSubprocessManager()
        flow = _make_linear_flow(workspace=str(tmp_path))

        orch_mgr = AsyncMock(spec=OrchestratorManager)
        orch_mgr.get_or_create = AsyncMock(side_effect=RuntimeError("Session init failed"))
        orch_mgr.terminate_all = AsyncMock()

        executor = FlowExecutor(
            db=db,
            event_callback=callback,
            subprocess_mgr=mock_sub,
            orchestrator_mgr=orch_mgr,
        )

        await executor.execute(flow, {}, str(tmp_path))

        # get_or_create should have been called only once (first task),
        # then disabled for subsequent tasks
        assert orch_mgr.get_or_create.call_count == 1

        # All tasks should have used direct subprocess
        assert len(mock_sub.calls) >= 2  # at least work + finish after fallback


# ---------------------------------------------------------------------------
# Tests: cancel with orchestrator
# ---------------------------------------------------------------------------


class TestCancelWithOrchestrator:
    @pytest.mark.asyncio
    async def test_cancel_terminates_orchestrator_sessions(self, tmp_path: Path) -> None:
        """Cancelling a flow terminates orchestrator sessions."""
        db = FlowstateDB(":memory:")
        _events, callback = _collect_events()
        mock_sub = MockSubprocessManager()

        orch_mgr = AsyncMock(spec=OrchestratorManager)
        orch_mgr.terminate_all = AsyncMock()

        executor = FlowExecutor(
            db=db,
            event_callback=callback,
            subprocess_mgr=mock_sub,
            orchestrator_mgr=orch_mgr,
        )

        # We need to start a flow and then cancel it. Since the mock completes
        # instantly, we'll create a flow run manually and test cancel directly.
        flow_def_id = db.create_flow_definition(name="test", source_dsl="", ast_json="{}")
        flow_run_id = db.create_flow_run(
            flow_definition_id=flow_def_id,
            data_dir=str(tmp_path),
            budget_seconds=3600,
            on_error="pause",
            default_workspace=str(tmp_path),
        )
        db.update_flow_run_status(flow_run_id, "running")

        await executor.cancel(flow_run_id)

        orch_mgr.terminate_all.assert_called_once_with(flow_run_id)
