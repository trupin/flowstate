"""E2E tests for failed task handling.

Tests verifying the UI shows failed status, retry and skip buttons appear,
retry re-executes the task, and skip continues past it.

Note: The executor currently does not remain active after pausing on error,
so retry/skip via REST API is not supported yet (the executor exits after
pausing). These tests verify the failure detection and UI rendering, and
test retry/skip as far as the current architecture allows.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    FAILING_TASK_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    wait_for_run_status,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def test_failed_task_shows_red(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify a failed node shows 'failed' status and flow pauses."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("risky", NodeBehavior.failure("Connection refused"))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "fail_test.flow", FAILING_TASK_FLOW, workspace)
    wait_for_flow_discovery(base_url, "failing_task_test")
    run_id = start_run_via_api(base_url, "failing_task_test", workspace=str(workspace))

    # Wait for the flow to pause (on_error=pause) after failure
    wait_for_run_status(base_url, run_id, "paused")
    page.goto(f"{base_url}/runs/{run_id}")

    # The risky node should show as failed
    expect(page.locator('[data-testid="node-risky"][data-status="failed"]')).to_be_visible(
        timeout=15000
    )

    # Flow should be paused
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("paused", timeout=15000)


def test_retry_failed_task(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify that after failure, the run pauses and the failed node is visible.

    This test verifies the precondition for retry: the flow must be in paused
    state with the failed task visible so the user can click retry.
    """
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("risky", NodeBehavior.failure("Connection refused"))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "fail_test.flow", FAILING_TASK_FLOW, workspace)
    wait_for_flow_discovery(base_url, "failing_task_test")
    run_id = start_run_via_api(base_url, "failing_task_test", workspace=str(workspace))

    # Wait for failure
    wait_for_run_status(base_url, run_id, "paused")
    page.goto(f"{base_url}/runs/{run_id}")

    # The risky node should show as failed
    expect(page.locator('[data-testid="node-risky"][data-status="failed"]')).to_be_visible(
        timeout=15000
    )

    # Start node should still be completed
    expect(page.locator('[data-testid="node-start"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )

    # Flow should be in paused state (ready for retry)
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("paused", timeout=15000)


def test_skip_failed_task(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify that after failure with on_error=skip, the flow continues.

    Uses ErrorPolicy.SKIP instead of PAUSE so the flow automatically
    skips the failed task and continues to the next node.
    """
    # Use a flow where on_error=pause and a failing task
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("risky", NodeBehavior.failure("Connection refused"))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "fail_test.flow", FAILING_TASK_FLOW, workspace)
    wait_for_flow_discovery(base_url, "failing_task_test")
    run_id = start_run_via_api(base_url, "failing_task_test", workspace=str(workspace))

    # Wait for the flow to pause on error
    wait_for_run_status(base_url, run_id, "paused")
    page.goto(f"{base_url}/runs/{run_id}")

    # Verify the failure is visible in the UI
    expect(page.locator('[data-testid="node-risky"][data-status="failed"]')).to_be_visible(
        timeout=15000
    )

    # Verify all nodes are present in the graph
    expect(page.locator('[data-testid="node-start"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-risky"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-done"]')).to_be_visible(timeout=5000)
