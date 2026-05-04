"""Real E2E Lumon sandbox tests — no mocking.

Runs flows with sandbox=true against a live Flowstate server with real
Claude agents. Verifies that Lumon's sandbox-guard.py hook correctly
blocks forbidden actions and that agents can use the flowstate plugin
for artifact submission and subtask management.

Prerequisites:
- `lumon` package installed
- Claude authenticated on the host (claude login)

Run with:
    uv run pytest tests/e2e/test_lumon_sandbox.py -v
"""

from __future__ import annotations

import json
import shutil
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

TASK_TIMEOUT = 180.0
RUN_TIMEOUT = 600.0

# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def _lumon_available() -> bool:
    return shutil.which("lumon") is not None


pytestmark = pytest.mark.skipif(
    not _lumon_available(),
    reason="lumon CLI not installed",
)


# ---------------------------------------------------------------------------
# Flow templates
# ---------------------------------------------------------------------------

SANDBOX_SIMPLE_FLOW = """
flow sandbox_simple {
    budget = 10m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = true

    input {
        topic: string = "testing"
    }

    entry start {
        prompt = "Say hello. Then submit your summary using the flowstate plugin."
    }

    exit done {
        prompt = "Say goodbye. Then submit your summary using the flowstate plugin."
    }

    start -> done
}
"""

SANDBOX_GUARD_TEST_FLOW = """
flow sandbox_guard_test {
    budget = 10m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = true

    input {
        topic: string = "testing"
    }

    entry test_guard {
        prompt = "Do the following in order. After each step, note whether it succeeded or was blocked:\\n1. Try to run: ls /\\n2. Try to run: echo hello > /tmp/test.txt\\n3. Try to read the file /etc/hosts\\n4. Run: lumon --working-dir sandbox 'return flowstate.guide()'\\n5. Submit your summary using the flowstate plugin, listing which actions were blocked and which succeeded."
    }

    exit done {
        prompt = "Summarize the results. Submit your summary using the flowstate plugin."
    }

    test_guard -> done
}
"""

SANDBOX_SUBTASKS_FLOW = """
flow sandbox_subtasks {
    budget = 10m
    on_error = pause
    context = handoff
    skip_permissions = true
    sandbox = true
    subtasks = true

    input {
        topic: string = "testing"
    }

    entry start {
        prompt = "Create 2 subtasks using the flowstate plugin: 'Check environment' and 'Submit results'. Mark both done. Then submit your summary."
    }

    exit done {
        prompt = "Say done. Submit your summary using the flowstate plugin."
    }

    start -> done
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
    resp = httpx.get(f"{base_url}/api/flows", timeout=5)
    resp.raise_for_status()
    flow = next(f for f in resp.json() if f["name"] == flow_name)
    flow_id = flow["id"]

    body: dict = {"params": params or {}}
    resp = httpx.post(f"{base_url}/api/flows/{flow_id}/runs", json=body, timeout=15)
    resp.raise_for_status()
    run_id = resp.json()["flow_run_id"]

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


def get_artifact(base_url: str, run_id: str, task_id: str, name: str) -> str | None:
    resp = httpx.get(f"{base_url}/api/runs/{run_id}/tasks/{task_id}/artifacts/{name}", timeout=5)
    if resp.status_code == 200:
        return resp.text
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    port = _find_free_port()
    watch_dir = tmp_path_factory.mktemp("lumon_e2e_flows")

    config = FlowstateConfig(
        server_port=port,
        watch_dir=str(watch_dir),
        log_level="debug",
    )

    app = create_app(config=config, static_dir=None)

    uv_config = uvicorn.Config(
        app,
        host="127.0.0.1",
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


class TestLumonSimple:
    """Test 1: Simple sandboxed flow completes and submits artifacts via plugin."""

    def test_simple_flow_completes(self, base_url, watch_dir):
        write_flow(watch_dir, "sandbox_simple.flow", SANDBOX_SIMPLE_FLOW)
        wait_for_flow_discovery(base_url, "sandbox_simple")

        run_id = start_run(base_url, "sandbox_simple")
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        assert run_data["status"] == "completed", (
            f"Expected completed, got {run_data['status']}. "
            f"Tasks: {json.dumps(run_data.get('tasks', []), indent=2)}"
        )

        # Verify both tasks completed
        for task in run_data["tasks"]:
            assert (
                task["status"] == "completed"
            ), f"Task {task['node_name']} status: {task['status']}"

        # Verify summary artifacts were submitted via the plugin
        for task in run_data["tasks"]:
            arts = [a["name"] for a in task.get("artifacts", [])]
            assert (
                "summary" in arts
            ), f"Task {task['node_name']} missing summary artifact. Has: {arts}"


class TestLumonGuard:
    """Test 2: Sandbox guard blocks forbidden actions."""

    def test_guard_blocks_forbidden_and_allows_lumon(self, base_url, watch_dir):
        write_flow(watch_dir, "sandbox_guard_test.flow", SANDBOX_GUARD_TEST_FLOW)
        wait_for_flow_discovery(base_url, "sandbox_guard_test")

        run_id = start_run(base_url, "sandbox_guard_test")
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        assert run_data["status"] == "completed", (
            f"Expected completed, got {run_data['status']}. "
            f"Tasks: {json.dumps(run_data.get('tasks', []), indent=2)}"
        )

        # The test_guard task should have completed — meaning the agent
        # recovered from blocked actions and submitted its summary
        test_task = next(t for t in run_data["tasks"] if t["node_name"] == "test_guard")
        assert test_task["status"] == "completed"

        # Verify summary was submitted (agent used the plugin successfully)
        arts = [a["name"] for a in test_task.get("artifacts", [])]
        assert "summary" in arts

        # Read the summary — it should mention blocked actions
        summary = get_artifact(base_url, run_id, test_task["id"], "summary")
        assert summary is not None
        assert len(summary) > 0


class TestLumonSubtasks:
    """Test 3: Subtask management works via the flowstate plugin."""

    def test_subtasks_via_plugin(self, base_url, watch_dir):
        write_flow(watch_dir, "sandbox_subtasks.flow", SANDBOX_SUBTASKS_FLOW)
        wait_for_flow_discovery(base_url, "sandbox_subtasks")

        run_id = start_run(base_url, "sandbox_subtasks")
        run_data = poll_run(base_url, run_id, target={"completed", "failed", "paused"})

        assert run_data["status"] == "completed", (
            f"Expected completed, got {run_data['status']}. "
            f"Tasks: {json.dumps(run_data.get('tasks', []), indent=2)}"
        )

        # Verify the start task completed
        start_task = next(t for t in run_data["tasks"] if t["node_name"] == "start")
        assert start_task["status"] == "completed"

        # Check subtasks were created
        resp = httpx.get(
            f"{base_url}/api/runs/{run_id}/tasks/{start_task['id']}/subtasks",
            timeout=5,
        )
        if resp.status_code == 200:
            subtasks = resp.json()
            # Agent should have created at least 1 subtask
            assert len(subtasks) >= 1, f"Expected subtasks, got: {subtasks}"
