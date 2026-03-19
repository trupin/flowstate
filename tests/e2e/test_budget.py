"""E2E tests for budget tracking.

Tests verifying the budget progress bar updates as tasks consume time and
is visible during and after execution.
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


def test_budget_bar_visible(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify the budget bar is visible during and after execution."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "budget_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Budget bar should be visible
    budget_bar = page.locator('[data-testid="budget-bar"]')
    expect(budget_bar).to_be_visible(timeout=15000)


def test_budget_bar_updates(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify the budget bar shows progress as tasks complete."""
    # Use slow tasks to consume more wall-clock time
    mock_subprocess.configure_node("start", NodeBehavior.slow(lines=5, summary="Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.slow(lines=5, summary="Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.slow(lines=5, summary="Finalized."))

    write_flow(watch_dir, "budget_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for completion
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Completed", timeout=30000)

    # Budget bar should still be visible after completion
    budget_bar = page.locator('[data-testid="budget-bar"]')
    expect(budget_bar).to_be_visible(timeout=5000)
