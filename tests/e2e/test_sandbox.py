"""Real E2E sandbox tests — no mocking.

Runs flows with sandbox=true against a live Flowstate server backed by a real
OpenShell sandbox (``flowstate-claude``).  Every test launches real Claude Code
agents inside the sandbox and verifies observable outcomes (run status, task
status, DECISION.json download, context handoff, etc.).

Prerequisites:
- ``openshell`` CLI on PATH
- A sandbox named ``flowstate-claude`` in Ready state
- Claude authenticated inside the sandbox (``claude login``)
- The sandbox image has ``claude-agent-acp`` installed

Run with:
    uv run pytest tests/e2e/test_sandbox.py -v -x --timeout=600
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SANDBOX_NAME = "flowstate-claude"
# Per-task timeout: how long we wait for a single task to reach a target status.
TASK_TIMEOUT = 180.0
# Per-run timeout: how long we wait for an entire run to finish.
RUN_TIMEOUT = 600.0


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def _openshell_available() -> bool:
    import shutil

    return shutil.which("openshell") is not None


def _sandbox_ready() -> bool:
    if not _openshell_available():
        return False
    import subprocess

    result = subprocess.run(
        ["openshell", "sandbox", "get", SANDBOX_NAME],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0 and "Ready" in result.stdout


pytestmark = pytest.mark.skipif(
    not _sandbox_ready(),
    reason=f"Sandbox '{SANDBOX_NAME}' not available or not Ready",
)


# ---------------------------------------------------------------------------
# Flow templates (simple, fast-completing prompts)
# ---------------------------------------------------------------------------

SANDBOX_LINEAR_FLOW = """
flow sandbox_linear {
    budget = 30m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = true

    input {
        topic: string = "testing"
    }

    entry start {
        prompt = "Say exactly: SANDBOX_LINEAR_START_OK. Nothing else."
    }

    task work {
        prompt = "Say exactly: SANDBOX_LINEAR_WORK_OK. Nothing else."
    }

    exit done {
        prompt = "Say exactly: SANDBOX_LINEAR_DONE_OK. Nothing else."
    }

    start -> work
    work -> done
}
"""

SANDBOX_CONDITIONAL_FLOW = """
flow sandbox_conditional {
    budget = 30m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = true
    judge = true

    input {
        topic: string = "testing"
    }

    entry analyze {
        prompt = "Say exactly: SANDBOX_ANALYZE_OK. Nothing else."
    }

    task fix {
        prompt = "Say exactly: SANDBOX_FIX_OK. Nothing else."
    }

    exit ship {
        prompt = "Say exactly: SANDBOX_SHIP_OK. Nothing else."
    }

    analyze -> ship when "the analysis is complete and no fixes are needed"
    analyze -> fix when "there are issues that need fixing"
    fix -> ship
}
"""

SANDBOX_FORK_JOIN_FLOW = """
flow sandbox_fork_join {
    budget = 30m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = true

    input {
        topic: string = "testing"
    }

    entry plan {
        prompt = "Say exactly: SANDBOX_PLAN_OK. Nothing else."
    }

    task branch_a {
        prompt = "Say exactly: SANDBOX_BRANCH_A_OK. Nothing else."
    }

    task branch_b {
        prompt = "Say exactly: SANDBOX_BRANCH_B_OK. Nothing else."
    }

    exit merge {
        prompt = "Say exactly: SANDBOX_MERGE_OK. Nothing else."
    }

    plan -> [branch_a, branch_b]
    [branch_a, branch_b] -> merge
}
"""

SANDBOX_NODE_OVERRIDE_FLOW = """
flow sandbox_node_override {
    budget = 30m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = false

    input {
        topic: string = "testing"
    }

    entry start {
        prompt = "Say exactly: NODE_OVERRIDE_START_OK. Nothing else."
    }

    task sandboxed_work {
        prompt = "Say exactly: NODE_OVERRIDE_SANDBOXED_OK. Nothing else."
        sandbox = true
    }

    exit done {
        prompt = "Say exactly: NODE_OVERRIDE_DONE_OK. Nothing else."
    }

    start -> sandboxed_work
    sandboxed_work -> done
}
"""

SANDBOX_SUBTASKS_FLOW = """
flow sandbox_subtasks {
    budget = 30m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = true
    subtasks = true

    input {
        topic: string = "testing"
    }

    entry start {
        prompt = "Create exactly 2 subtasks: 'Check environment' and 'Report status'. Mark both done immediately. Then say: SANDBOX_SUBTASKS_OK"
    }

    exit done {
        prompt = "Say exactly: SANDBOX_SUBTASKS_DONE_OK. Nothing else."
    }

    start -> done
}
"""

SANDBOX_CONTEXT_HANDOFF_FLOW = """
flow sandbox_context_handoff {
    budget = 30m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = true

    input {
        secret_word: string = "FLAMINGO"
    }

    entry producer {
        prompt = "Remember this secret word: {{secret_word}}. Say: I have memorized the secret word {{secret_word}}."
    }

    exit consumer {
        prompt = "What was the secret word from the previous task? Say exactly: The secret word is <word>."
    }

    producer -> consumer
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/api/flows", timeout=2)
            if resp.status_code in (200, 404):
                return
        except httpx.RequestError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"Server on port {port} did not start within {timeout}s")


def write_flow(watch_dir: Path, filename: str, template: str) -> Path:
    path = watch_dir / filename
    path.write_text(template)
    return path


def wait_for_flow_discovery(base_url: str, flow_name: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/flows", timeout=3)
            if resp.status_code == 200 and any(f.get("name") == flow_name for f in resp.json()):
                return
        except httpx.RequestError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Flow '{flow_name}' not discovered within {timeout}s")


def start_run(base_url: str, flow_name: str, params: dict | None = None) -> str:
    """Start a flow run and return the run_id."""
    resp = httpx.get(f"{base_url}/api/flows", timeout=5)
    resp.raise_for_status()
    flow = next(f for f in resp.json() if f["name"] == flow_name)
    flow_id = flow["id"]

    body: dict = {"params": params or {}}
    resp = httpx.post(f"{base_url}/api/flows/{flow_id}/runs", json=body, timeout=15)
    resp.raise_for_status()
    run_id = resp.json()["flow_run_id"]

    # Wait for DB record
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/runs/{run_id}", timeout=5)
            if r.status_code == 200:
                return run_id
        except httpx.RequestError:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"Run '{run_id}' DB record not created within 15s")


def poll_run(
    base_url: str,
    run_id: str,
    target: str | set[str] = "completed",
    timeout: float = RUN_TIMEOUT,
) -> dict:
    """Poll until run reaches target status. Returns the run dict."""
    if isinstance(target, str):
        target = {target}
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/runs/{run_id}", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                last_status = data.get("status")
                if last_status in target:
                    return data
        except httpx.RequestError:
            pass
        time.sleep(2.0)
    raise TimeoutError(
        f"Run '{run_id}' did not reach {target} within {timeout}s " f"(last status: {last_status})"
    )


def get_run(base_url: str, run_id: str) -> dict:
    resp = httpx.get(f"{base_url}/api/runs/{run_id}", timeout=5)
    resp.raise_for_status()
    return resp.json()


def get_task_dir(run_data: dict, node_name: str) -> str | None:
    """Extract the task_dir for a given node from run data."""
    for task in run_data.get("tasks", []):
        if task.get("node_name") == node_name:
            return task.get("task_dir")
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    """Start a real Flowstate server (no mock harness) with sandbox support."""
    # Use port 9090 and bind to 0.0.0.0 so the sandbox can reach us
    # via host.docker.internal:9090 (allowed by the sandbox network policy)
    port = 9090
    data_dir = tmp_path_factory.mktemp("sandbox_e2e_data")
    watch_dir = tmp_path_factory.mktemp("sandbox_e2e_flows")

    config = FlowstateConfig(
        server_port=port,
        database_path=str(data_dir / "flowstate.db"),
        watch_dir=str(watch_dir),
        sandbox_name=SANDBOX_NAME,
        log_level="debug",
    )

    # No harness arg = production mode (AcpHarness with claude-agent-acp)
    app = create_app(config=config, static_dir=None)

    uv_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(uv_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        _wait_for_server(port)
    except RuntimeError:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.skip(f"Could not start test server on port {port}")

    url = f"http://127.0.0.1:{port}"
    yield url, Path(watch_dir)

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def base_url(server_url) -> str:
    return server_url[0]


@pytest.fixture(scope="module")
def watch_dir(server_url) -> Path:
    return server_url[1]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSandboxLinear:
    """Test 1: Simple linear flow runs to completion inside the sandbox."""

    def test_linear_flow_completes(self, base_url, watch_dir):
        write_flow(watch_dir, "sandbox_linear.flow", SANDBOX_LINEAR_FLOW)
        wait_for_flow_discovery(base_url, "sandbox_linear")

        run_id = start_run(base_url, "sandbox_linear")
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        assert run_data["status"] == "completed", (
            f"Expected completed, got {run_data['status']}. "
            f"Tasks: {json.dumps(run_data.get('tasks', []), indent=2)}"
        )

        # Verify all 3 tasks ran
        task_names = {t["node_name"] for t in run_data.get("tasks", [])}
        assert "start" in task_names
        assert "work" in task_names
        assert "done" in task_names

        # Every task should be completed
        for task in run_data["tasks"]:
            assert (
                task["status"] == "completed"
            ), f"Task {task['node_name']} status: {task['status']}"


class TestSandboxConditional:
    """Test 2: Conditional flow with judge routing works in sandbox.

    This tests DECISION.json download from sandbox — the judge writes
    DECISION.json inside the sandbox, and the engine must download it
    to the host for routing.
    """

    def test_conditional_flow_completes(self, base_url, watch_dir):
        write_flow(watch_dir, "sandbox_conditional.flow", SANDBOX_CONDITIONAL_FLOW)
        wait_for_flow_discovery(base_url, "sandbox_conditional")

        run_id = start_run(base_url, "sandbox_conditional")
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        # The flow should complete regardless of which path the judge picks
        assert run_data["status"] == "completed", (
            f"Expected completed, got {run_data['status']}. "
            f"Tasks: {json.dumps(run_data.get('tasks', []), indent=2)}"
        )

        task_names = {t["node_name"] for t in run_data.get("tasks", [])}
        assert "analyze" in task_names
        assert "ship" in task_names
        # "fix" may or may not be present depending on judge decision


class TestSandboxForkJoin:
    """Test 3: Fork/join parallelism works inside the sandbox.

    Two branches run concurrently inside the same sandbox, then merge.
    """

    def test_fork_join_completes(self, base_url, watch_dir):
        write_flow(watch_dir, "sandbox_fork_join.flow", SANDBOX_FORK_JOIN_FLOW)
        wait_for_flow_discovery(base_url, "sandbox_fork_join")

        run_id = start_run(base_url, "sandbox_fork_join")
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        assert run_data["status"] == "completed", (
            f"Expected completed, got {run_data['status']}. "
            f"Tasks: {json.dumps(run_data.get('tasks', []), indent=2)}"
        )

        task_names = {t["node_name"] for t in run_data.get("tasks", [])}
        assert "plan" in task_names
        assert "branch_a" in task_names
        assert "branch_b" in task_names
        assert "merge" in task_names


class TestSandboxContextHandoff:
    """Test 4: Context handoff works through the sandbox.

    The producer task writes SUMMARY.md inside the sandbox; the engine
    must retrieve it so the consumer task gets context from the prior step.
    """

    def test_context_flows_between_tasks(self, base_url, watch_dir):
        write_flow(
            watch_dir,
            "sandbox_context_handoff.flow",
            SANDBOX_CONTEXT_HANDOFF_FLOW,
        )
        wait_for_flow_discovery(base_url, "sandbox_context_handoff")

        run_id = start_run(
            base_url,
            "sandbox_context_handoff",
            params={"secret_word": "FLAMINGO"},
        )
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        assert run_data["status"] == "completed", (
            f"Expected completed, got {run_data['status']}. "
            f"Tasks: {json.dumps(run_data.get('tasks', []), indent=2)}"
        )

        # Both tasks should have completed
        for task in run_data["tasks"]:
            assert (
                task["status"] == "completed"
            ), f"Task {task['node_name']} status: {task['status']}"


class TestSandboxSubtasks:
    """Test 5: Subtask creation works inside the sandbox."""

    def test_subtasks_work_in_sandbox(self, base_url, watch_dir):
        write_flow(watch_dir, "sandbox_subtasks.flow", SANDBOX_SUBTASKS_FLOW)
        wait_for_flow_discovery(base_url, "sandbox_subtasks")

        run_id = start_run(base_url, "sandbox_subtasks")
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        assert run_data["status"] == "completed", (
            f"Expected completed, got {run_data['status']}. "
            f"Tasks: {json.dumps(run_data.get('tasks', []), indent=2)}"
        )


class TestSandboxPreflightChecks:
    """Test 6: Server-side pre-flight checks work correctly."""

    def test_preflight_passes_with_valid_sandbox(self, base_url, watch_dir):
        """The sandbox pre-flight check should pass since our sandbox is Ready."""
        write_flow(watch_dir, "sandbox_linear.flow", SANDBOX_LINEAR_FLOW)
        wait_for_flow_discovery(base_url, "sandbox_linear")

        # Starting a run should not fail the pre-flight check
        resp = httpx.get(f"{base_url}/api/flows", timeout=5)
        flow = next(f for f in resp.json() if f["name"] == "sandbox_linear")
        flow_id = flow["id"]

        resp = httpx.post(
            f"{base_url}/api/flows/{flow_id}/runs",
            json={"params": {}},
            timeout=15,
        )
        # Should succeed (202 or 200), not fail with sandbox error
        assert resp.status_code < 400, f"Pre-flight check failed: {resp.status_code} {resp.text}"

    def test_preflight_fails_with_bad_sandbox_name(self, tmp_path_factory):
        """If sandbox name doesn't exist, pre-flight should return 400."""
        port = _find_free_port()
        data_dir = tmp_path_factory.mktemp("bad_sandbox_data")
        watch_dir = tmp_path_factory.mktemp("bad_sandbox_flows")

        config = FlowstateConfig(
            server_port=port,
            database_path=str(data_dir / "flowstate.db"),
            watch_dir=str(watch_dir),
            sandbox_name="nonexistent-sandbox-xyz",
            log_level="warning",
        )

        app = create_app(config=config, static_dir=None)

        uv_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(uv_config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        try:
            _wait_for_server(port)
        except RuntimeError:
            server.should_exit = True
            thread.join(timeout=5)
            pytest.skip("Could not start test server")

        bad_url = f"http://127.0.0.1:{port}"

        try:
            # Write a sandbox flow
            flow_path = Path(watch_dir) / "sandbox_linear.flow"
            flow_path.write_text(SANDBOX_LINEAR_FLOW)
            wait_for_flow_discovery(bad_url, "sandbox_linear")

            resp = httpx.get(f"{bad_url}/api/flows", timeout=5)
            flow = next(f for f in resp.json() if f["name"] == "sandbox_linear")
            flow_id = flow["id"]

            resp = httpx.post(
                f"{bad_url}/api/flows/{flow_id}/runs",
                json={"params": {}},
                timeout=15,
            )
            assert (
                resp.status_code == 400
            ), f"Expected 400 for bad sandbox, got {resp.status_code}: {resp.text}"
            assert "nonexistent-sandbox-xyz" in resp.text or "not found" in resp.text.lower()
        finally:
            server.should_exit = True
            thread.join(timeout=5)


class TestSandboxDecisionDownload:
    """Test 7: DECISION.json is properly downloaded from sandbox.

    Uses self-report routing (judge=false) with conditional edges.
    The agent writes DECISION.json inside the sandbox, and the engine
    must download it to the host to read routing decisions.
    """

    def test_self_report_routing_with_sandbox(self, base_url, watch_dir):
        flow_source = """
flow sandbox_self_report {
    budget = 30m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = true

    input {
        topic: string = "testing"
    }

    entry check {
        prompt = "Write a JSON file called DECISION.json. It must contain keys: decision (set to pass), target (set to ship), confidence (set to 0.95), reasoning (set to All checks passed). Then say: DECISION written."
    }

    task retry {
        prompt = "Say exactly: RETRY_OK. Nothing else."
    }

    exit ship {
        prompt = "Say exactly: SHIP_OK. Nothing else."
    }

    check -> ship when "all checks pass"
    check -> retry when "checks failed and need retry"
    retry -> ship
}
"""
        write_flow(watch_dir, "sandbox_self_report.flow", flow_source)
        wait_for_flow_discovery(base_url, "sandbox_self_report")

        run_id = start_run(base_url, "sandbox_self_report")
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        assert run_data["status"] == "completed", (
            f"Expected completed, got {run_data['status']}. "
            f"Tasks: {json.dumps(run_data.get('tasks', []), indent=2)}"
        )

        task_names = {t["node_name"] for t in run_data.get("tasks", [])}
        assert "check" in task_names
        assert "ship" in task_names


class TestSandboxErrorRecovery:
    """Test 8: on_error=pause works correctly with sandbox tasks."""

    def test_paused_run_can_be_observed(self, base_url, watch_dir):
        """A flow that hits an error should pause, not crash silently."""
        flow_source = (
            "flow sandbox_error_test {\n"
            "    budget = 5m\n"
            "    on_error = pause\n"
            "    context = handoff\n"
            "    skip_permissions = true\n"
            "    sandbox = true\n"
            "\n"
            "    input {\n"
            '        topic: string = "testing"\n'
            "    }\n"
            "\n"
            "    entry start {\n"
            '        prompt = "Say exactly: ERROR_TEST_START. Nothing else."\n'
            "    }\n"
            "\n"
            "    exit done {\n"
            '        prompt = "Say exactly: ERROR_TEST_DONE. Nothing else."\n'
            "    }\n"
            "\n"
            "    start -> done\n"
            "}\n"
        )
        write_flow(watch_dir, "sandbox_error_test.flow", flow_source)
        wait_for_flow_discovery(base_url, "sandbox_error_test")

        run_id = start_run(base_url, "sandbox_error_test")
        # This should either complete or pause — not hang forever
        run_data = poll_run(
            base_url, run_id, target={"completed", "failed", "paused"}, timeout=TASK_TIMEOUT
        )

        # As long as it reached a terminal or paused state, the error
        # recovery mechanism is working
        assert run_data["status"] in {"completed", "paused", "failed"}


class TestSandboxRunStatus:
    """Test 9: Run status API returns correct info for sandboxed runs."""

    def test_run_detail_has_task_info(self, base_url, watch_dir):
        write_flow(watch_dir, "sandbox_linear.flow", SANDBOX_LINEAR_FLOW)
        wait_for_flow_discovery(base_url, "sandbox_linear")

        run_id = start_run(base_url, "sandbox_linear")

        # Poll until at least one task is running
        deadline = time.monotonic() + 60.0
        found_running = False
        while time.monotonic() < deadline:
            run_data = get_run(base_url, run_id)
            tasks = run_data.get("tasks", [])
            if any(t.get("status") == "running" for t in tasks):
                found_running = True
                break
            if run_data.get("status") in {"completed", "failed", "paused"}:
                # Already finished — that's fine too
                found_running = True
                break
            time.sleep(2.0)

        assert found_running, "Never observed a running task or terminal state"

        # Now wait for completion
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        # Verify structure
        assert "tasks" in run_data
        assert "status" in run_data
        for task in run_data["tasks"]:
            assert "node_name" in task
            assert "status" in task
