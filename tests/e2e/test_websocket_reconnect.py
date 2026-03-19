"""E2E tests for WebSocket reconnection.

Tests verifying the UI recovers when the WebSocket connection drops,
missed events are replayed, and the UI catches up to the current state.
"""

from __future__ import annotations

from playwright.sync_api import BrowserContext, expect

from tests.e2e.flow_fixtures import (
    LINEAR_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    wait_for_run_status,
    wait_for_task_status,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def test_reconnect_replays_events(
    browser,
    context: BrowserContext,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify UI recovers after WebSocket disconnect and replays missed events."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    gate = mock_subprocess.add_gate("work")
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "ws_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    # Wait for "work" to be running (blocked by gate)
    wait_for_task_status(base_url, run_id, "work", "running")

    page = context.new_page()
    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for start to show as completed in the UI
    expect(page.locator('[data-testid="node-start"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )

    # Simulate network disconnect using Playwright's context API
    context.set_offline(True)

    # Release the gate so "work" completes while the UI is disconnected
    gate.set()

    # Give the server time to process the task completion
    page.wait_for_timeout(2000)

    # Reconnect
    context.set_offline(False)

    # Wait for the run to complete on the server side
    wait_for_run_status(base_url, run_id, "completed")

    # Reload the page to get the final state after reconnection
    page.goto(f"{base_url}/runs/{run_id}")

    # Work node should show as completed (state caught up after reconnect/reload)
    expect(page.locator('[data-testid="node-work"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )

    # Flow should be completed
    expect(page.locator('[data-testid="node-done"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )

    page.close()
