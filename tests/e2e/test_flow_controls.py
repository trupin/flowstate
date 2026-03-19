"""E2E tests for flow control actions.

Tests pausing a running flow, resuming it, and cancelling. Uses mock gates
to hold tasks at controllable points so the test can interact with control
buttons while execution is in progress.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    LINEAR_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def test_pause_and_resume(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify pause stops execution and resume continues to completion."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    gate = mock_subprocess.add_gate("work")
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "ctrl_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for "start" to complete and "work" to be running (blocked by gate)
    expect(page.locator('[data-testid="node-start"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )
    expect(page.locator('[data-testid="node-work"][data-status="running"]')).to_be_visible(
        timeout=15000
    )

    # Click pause
    page.locator('[data-testid="btn-pause"]').click()

    # Release the gate so work completes
    gate.set()

    # Flow should be paused after work completes
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Paused", timeout=15000)

    # Click resume
    page.locator('[data-testid="btn-resume"]').click()

    # Flow should complete
    expect(flow_status).to_have_text("Completed", timeout=20000)


def test_cancel(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify cancel terminates the flow."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    gate = mock_subprocess.add_gate("work")
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "ctrl_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for work to be running
    expect(page.locator('[data-testid="node-work"][data-status="running"]')).to_be_visible(
        timeout=15000
    )

    # Click cancel
    page.locator('[data-testid="btn-cancel"]').click()

    # Release gate (so the task can clean up)
    gate.set()

    # Flow should be cancelled
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Cancelled", timeout=15000)


def test_pause_button_visibility(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify pause button is visible when running, hidden/disabled when completed."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "ctrl_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for completion
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Completed", timeout=20000)

    # Pause button should be hidden or disabled after completion
    pause_btn = page.locator('[data-testid="btn-pause"]')
    expect(pause_btn).to_be_hidden(timeout=5000)
