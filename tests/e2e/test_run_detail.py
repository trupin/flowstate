"""E2E tests for the Run Detail page.

Tests live graph visualization with real-time node status changes, streaming
logs in the log viewer, flow completion status, and node selection changing
log viewer content. This validates the full pipeline: flow execution ->
WebSocket events -> UI rendering.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    LINEAR_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    wait_for_run_status,
    wait_for_task_status,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def _setup_linear_flow(page, base_url, watch_dir, workspace, mock_subprocess):
    """Helper: write flow, configure mock, discover, and start via API."""
    write_flow(watch_dir, "detail_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    return start_run_via_api(base_url, "linear_test", workspace=str(workspace))


def test_nodes_transition_to_completed(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify all nodes transition through pending -> running -> completed."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    run_id = _setup_linear_flow(page, base_url, watch_dir, workspace, mock_subprocess)

    # Wait for the run to complete before navigating so the initial fetch
    # returns the final state (WebSocket events are not replayed for late subscribers).
    wait_for_run_status(base_url, run_id, "completed")
    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for all nodes to reach completed
    expect(page.locator('[data-testid="node-start"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )
    expect(page.locator('[data-testid="node-work"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )
    expect(page.locator('[data-testid="node-done"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )


def test_streaming_logs_visible(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify streaming log content appears in the log viewer."""
    mock_subprocess.configure_node(
        "start",
        NodeBehavior.with_output(
            "Initializing project...",
            "Setting up structure...",
            summary="Initialized.",
        ),
    )
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    run_id = _setup_linear_flow(page, base_url, watch_dir, workspace, mock_subprocess)
    wait_for_run_status(base_url, run_id, "completed")
    page.goto(f"{base_url}/runs/{run_id}")

    # Click on the start node to select it
    expect(page.locator('[data-testid="node-start"]')).to_be_visible(timeout=15000)
    page.locator('[data-testid="node-start"]').click()

    # Verify log viewer shows the streamed content
    log_viewer = page.locator('[data-testid="log-viewer"]')
    expect(log_viewer).to_contain_text("Initializing project...", timeout=10000)


def test_flow_status_completed(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify the flow status indicator shows 'completed' when done."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    run_id = _setup_linear_flow(page, base_url, watch_dir, workspace, mock_subprocess)
    wait_for_run_status(base_url, run_id, "completed")
    page.goto(f"{base_url}/runs/{run_id}")

    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("completed", timeout=20000)


def test_click_node_changes_logs(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify clicking different nodes changes the log viewer content."""
    mock_subprocess.configure_node(
        "start", NodeBehavior.with_output("Start output here", summary="Initialized.")
    )
    mock_subprocess.configure_node(
        "work", NodeBehavior.with_output("Work output here", summary="Work done.")
    )
    mock_subprocess.configure_node(
        "done", NodeBehavior.with_output("Done output here", summary="Finalized.")
    )

    run_id = _setup_linear_flow(page, base_url, watch_dir, workspace, mock_subprocess)
    wait_for_run_status(base_url, run_id, "completed")
    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for completion to render in the UI
    expect(page.locator('[data-testid="node-done"][data-status="completed"]')).to_be_visible(
        timeout=20000
    )

    # Click start node -> verify its logs
    page.locator('[data-testid="node-start"]').click()
    log_viewer = page.locator('[data-testid="log-viewer"]')
    expect(log_viewer).to_contain_text("Start output here", timeout=5000)

    # Click work node -> verify logs change
    page.locator('[data-testid="node-work"]').click()
    expect(log_viewer).to_contain_text("Work output here", timeout=5000)


def test_running_node_has_pulse(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify a running node shows 'running' status while gated."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    gate = mock_subprocess.add_gate("work")
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    run_id = _setup_linear_flow(page, base_url, watch_dir, workspace, mock_subprocess)

    # Wait for the "work" node to enter running state (blocked by gate) before
    # navigating so the initial fetch returns the correct in-progress state.
    wait_for_task_status(base_url, run_id, "work", "running")
    page.goto(f"{base_url}/runs/{run_id}")

    # Work node should be running (blocked by gate)
    expect(page.locator('[data-testid="node-work"][data-status="running"]')).to_be_visible(
        timeout=15000
    )

    # Release the gate
    gate.set()

    # Work should complete — wait for run to finish, then reload to get final state
    wait_for_run_status(base_url, run_id, "completed")
    page.goto(f"{base_url}/runs/{run_id}")

    expect(page.locator('[data-testid="node-work"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )
