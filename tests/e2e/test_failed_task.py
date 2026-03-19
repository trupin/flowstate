"""E2E tests for failed task handling.

Tests verifying the UI shows failed status, retry and skip buttons appear,
retry re-executes the task, and skip continues past it.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    FAILING_TASK_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
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

    page.goto(f"{base_url}/runs/{run_id}")

    # The risky node should show as failed
    expect(page.locator('[data-testid="node-risky"][data-status="failed"]')).to_be_visible(
        timeout=15000
    )

    # Flow should be paused (on_error=pause)
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Paused", timeout=15000)


def test_retry_failed_task(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify retry re-executes the failed task and flow completes on success."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("risky", NodeBehavior.failure("Connection refused"))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "fail_test.flow", FAILING_TASK_FLOW, workspace)
    wait_for_flow_discovery(base_url, "failing_task_test")
    run_id = start_run_via_api(base_url, "failing_task_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for failure
    expect(page.locator('[data-testid="node-risky"][data-status="failed"]')).to_be_visible(
        timeout=15000
    )

    # Reconfigure risky to succeed on retry
    mock_subprocess.configure_node("risky", NodeBehavior.success("Risky task succeeded."))

    # Click retry
    page.locator('[data-testid="btn-retry"]').click()

    # The risky node should re-execute and complete
    expect(page.locator('[data-testid="node-risky"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )

    # Flow should complete
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Completed", timeout=20000)


def test_skip_failed_task(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify skip marks the node as skipped and flow continues."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("risky", NodeBehavior.failure("Connection refused"))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "fail_test.flow", FAILING_TASK_FLOW, workspace)
    wait_for_flow_discovery(base_url, "failing_task_test")
    run_id = start_run_via_api(base_url, "failing_task_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for failure
    expect(page.locator('[data-testid="node-risky"][data-status="failed"]')).to_be_visible(
        timeout=15000
    )

    # Click skip
    page.locator('[data-testid="btn-skip"]').click()

    # Node should show as skipped
    expect(page.locator('[data-testid="node-risky"][data-status="skipped"]')).to_be_visible(
        timeout=15000
    )

    # Flow should continue to done and complete
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Completed", timeout=20000)
