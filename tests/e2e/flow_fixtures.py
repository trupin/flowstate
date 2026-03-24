"""Flow fixture templates for E2E tests.

Each template is a Python string constant in the Flowstate DSL format.
Templates use {workspace} as a placeholder for the workspace path,
which is substituted via str.format() at write time.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from pathlib import Path

# --- Flow Templates ---

LINEAR_FLOW = """\
flow linear_test {{
    budget = 1h
    on_error = pause
    context = handoff
    workspace = "{workspace}"

    input {{
        description: string = "test"
    }}

    entry start {{
        prompt = "Initialize the project. Set up the basic structure."
    }}

    task work {{
        prompt = "Do the main work. Implement the feature."
    }}

    exit done {{
        prompt = "Finalize everything. Write a summary."
    }}

    start -> work
    work -> done
}}
"""

FORK_JOIN_FLOW = """\
flow fork_join_test {{
    budget = 1h
    on_error = pause
    context = handoff
    workspace = "{workspace}"

    input {{
        description: string = "test"
    }}

    entry analyze {{
        prompt = "Analyze the codebase and create a plan."
    }}

    task test_unit {{
        prompt = "Run unit tests and fix any failures."
    }}

    task test_integration {{
        prompt = "Run integration tests and verify behavior."
    }}

    exit report {{
        prompt = "Write a report summarizing all test results."
    }}

    analyze -> [test_unit, test_integration]
    [test_unit, test_integration] -> report
}}
"""

CONDITIONAL_FLOW = """\
flow conditional_test {{
    budget = 2h
    on_error = pause
    context = handoff
    judge = true
    workspace = "{workspace}"

    input {{
        description: string = "test"
    }}

    entry implement {{
        prompt = "Implement the requested changes."
    }}

    task review {{
        prompt = "Review all changes for correctness and quality."
    }}

    exit ship {{
        prompt = "Ship the changes. Write release notes."
    }}

    implement -> review
    review -> ship when "all changes are approved and tests pass"
    review -> implement when "changes need more work"
}}
"""

CYCLE_FLOW = """\
flow cycle_test {{
    budget = 3h
    on_error = pause
    context = handoff
    judge = true
    workspace = "{workspace}"

    input {{
        description: string = "test"
    }}

    entry plan {{
        prompt = "Create an implementation plan."
    }}

    task implement {{
        prompt = "Implement changes according to the plan."
    }}

    task verify {{
        prompt = "Verify the implementation meets requirements."
    }}

    exit complete {{
        prompt = "Finalize and document the completed work."
    }}

    plan -> implement
    implement -> verify
    verify -> complete when "all requirements are met"
    verify -> implement when "more work is needed"
}}
"""

PARAMETERIZED_FLOW = """\
flow parameterized_test {{
    budget = 1h
    on_error = pause
    context = handoff
    workspace = "{workspace}"

    input {{
        focus: string = "all"
        verbose: bool = false
    }}

    entry start {{
        prompt = "Analyze the codebase. Focus on: {{{{focus}}}}."
    }}

    task work {{
        prompt = "Implement improvements for {{{{focus}}}}. Verbose: {{{{verbose}}}}."
    }}

    exit done {{
        prompt = "Write a summary of changes."
    }}

    start -> work
    work -> done
}}
"""

FAILING_TASK_FLOW = """\
flow failing_task_test {{
    budget = 1h
    on_error = pause
    context = handoff
    workspace = "{workspace}"

    input {{
        description: string = "test"
    }}

    entry start {{
        prompt = "Initialize the project."
    }}

    task risky {{
        prompt = "Attempt a risky operation that may fail."
    }}

    exit done {{
        prompt = "Finalize the results."
    }}

    start -> risky
    risky -> done
}}
"""

INVALID_FLOW = """\
this is not valid DSL syntax at all!!!
{{{{ totally broken }}}}
"""

FLOW_WITH_TYPE_ERROR = """\
flow missing_exit {{
    budget = 1h
    on_error = pause
    context = handoff
    workspace = "{workspace}"

    input {{
        description: string = "test"
    }}

    entry start {{
        prompt = "This flow has no exit node."
    }}

    task work {{
        prompt = "Do some work."
    }}

    start -> work
}}
"""


# --- Helpers ---


def write_flow(
    watch_dir: Path,
    filename: str,
    template: str,
    workspace: Path | str,
) -> Path:
    """Write a flow file to the watch directory.

    Args:
        watch_dir: The directory being watched by the file watcher.
        filename: The filename (e.g., "my_flow.flow").
        template: One of the template constants above.
        workspace: The workspace path to substitute into the template.

    Returns:
        The path to the written file.
    """
    content = template.format(workspace=str(workspace))
    path = watch_dir / filename
    path.write_text(content)
    return path


def wait_for_flow_discovery(
    base_url: str,
    flow_name: str,
    timeout: float = 5.0,
) -> None:
    """Poll GET /api/flows until the named flow appears.

    Raises TimeoutError if the flow is not discovered within the timeout.

    Args:
        base_url: The server base URL (e.g., "http://localhost:18765").
        flow_name: The expected flow name.
        timeout: Maximum seconds to wait.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/flows", timeout=2)
            if resp.status_code == 200:
                flows = resp.json()
                if any(f.get("name") == flow_name for f in flows):
                    return
        except httpx.RequestError:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"Flow '{flow_name}' not discovered within {timeout}s")


def wait_for_flow_gone(
    base_url: str,
    flow_name: str,
    timeout: float = 5.0,
) -> None:
    """Poll GET /api/flows until the named flow disappears.

    Args:
        base_url: The server base URL.
        flow_name: The flow name to wait for removal.
        timeout: Maximum seconds to wait.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/flows", timeout=2)
            if resp.status_code == 200:
                flows = resp.json()
                if not any(f.get("name") == flow_name for f in flows):
                    return
        except httpx.RequestError:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"Flow '{flow_name}' still present after {timeout}s")


def wait_for_flow_status(
    base_url: str,
    flow_name: str,
    status: str,
    timeout: float = 5.0,
) -> None:
    """Poll GET /api/flows until a flow has the expected validity status.

    Args:
        base_url: The server base URL.
        flow_name: The flow name to check.
        status: Expected status (e.g., "valid", "error").
        timeout: Maximum seconds to wait.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/flows", timeout=2)
            if resp.status_code == 200:
                for f in resp.json():
                    if f.get("name") == flow_name and f.get("status") == status:
                        return
        except httpx.RequestError:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"Flow '{flow_name}' did not reach status '{status}' within {timeout}s")


def wait_for_flow_status_by_id(
    base_url: str,
    flow_id: str,
    status: str,
    timeout: float = 5.0,
) -> None:
    """Poll GET /api/flows until a flow (matched by ID) has the expected status.

    Unlike wait_for_flow_status which matches by name, this matches by the
    flow's ``id`` field (which is the file stem). Useful when the flow has
    parse errors and its ``name`` is None.

    Args:
        base_url: The server base URL.
        flow_id: The flow ID (file stem) to check.
        status: Expected status (e.g., "valid", "error").
        timeout: Maximum seconds to wait.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/flows", timeout=2)
            if resp.status_code == 200:
                for f in resp.json():
                    if f.get("id") == flow_id and f.get("status") == status:
                        return
        except httpx.RequestError:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"Flow id='{flow_id}' did not reach status '{status}' within {timeout}s")


def start_run_via_api(
    base_url: str,
    flow_name: str,
    params: dict[str, str | float | bool] | None = None,
    workspace: str | None = None,
) -> str:
    """Start a flow run via the REST API.

    Returns the flow_run_id. The route handler and executor now share the same
    ID, so we can use the response directly. We poll GET /api/runs/{id} to wait
    for the DB record to be created (happens asynchronously in the background task).

    Args:
        base_url: The server base URL.
        flow_name: The name of the flow to run.
        params: Optional parameters for the flow.
        workspace: Optional workspace path override.

    Returns:
        The flow_run_id string.
    """
    # Get the flow ID
    resp = httpx.get(f"{base_url}/api/flows", timeout=5)
    resp.raise_for_status()
    flows = resp.json()
    flow = next(f for f in flows if f["name"] == flow_name)
    flow_id = flow["id"]

    body: dict[str, object] = {"params": params or {}}
    if workspace:
        body["workspace_path"] = workspace

    resp = httpx.post(f"{base_url}/api/flows/{flow_id}/runs", json=body, timeout=10)
    resp.raise_for_status()
    run_id = resp.json()["flow_run_id"]

    # Wait for the DB record to be created (executor runs asynchronously)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/runs/{run_id}", timeout=5)
            if resp.status_code == 200:
                return run_id
        except httpx.RequestError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Run '{run_id}' not found in DB within 10s")


def wait_for_run_status(
    base_url: str,
    run_id: str,
    target_status: str | set[str] = "completed",
    timeout: float = 10.0,
) -> None:
    """Poll GET /api/runs/{run_id} until the run reaches the target status.

    Args:
        base_url: The server base URL.
        run_id: The flow run ID to poll.
        target_status: A status string or set of status strings to wait for.
        timeout: Maximum seconds to wait.
    """
    if isinstance(target_status, str):
        target_status = {target_status}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/runs/{run_id}", timeout=5)
            if resp.status_code == 200:
                status = resp.json().get("status")
                if status in target_status:
                    return
        except httpx.RequestError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Run '{run_id}' did not reach status {target_status} within {timeout}s")


def wait_for_task_status(
    base_url: str,
    run_id: str,
    node_name: str,
    target_status: str | set[str],
    timeout: float = 10.0,
) -> None:
    """Poll GET /api/runs/{run_id} until a specific task reaches the target status.

    Args:
        base_url: The server base URL.
        run_id: The flow run ID.
        node_name: The node name to check.
        target_status: A status string or set of status strings to wait for.
        timeout: Maximum seconds to wait.
    """
    if isinstance(target_status, str):
        target_status = {target_status}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/runs/{run_id}", timeout=5)
            if resp.status_code == 200:
                tasks = resp.json().get("tasks", [])
                for t in tasks:
                    if t.get("node_name") == node_name and t.get("status") in target_status:
                        return
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    raise TimeoutError(
        f"Task '{node_name}' in run '{run_id}' did not reach status "
        f"{target_status} within {timeout}s"
    )
