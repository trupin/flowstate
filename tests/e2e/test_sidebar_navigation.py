"""E2E tests for sidebar navigation.

Tests clicking flows in sidebar shows graph previews, active runs appear
in sidebar, and clicking active runs opens Run Detail.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    FORK_JOIN_FLOW,
    LINEAR_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def test_click_flow_shows_preview(page: Page, base_url: str, watch_dir, workspace):
    """Verify clicking a flow in sidebar shows its graph preview."""
    write_flow(watch_dir, "nav_a.flow", LINEAR_FLOW, workspace)
    write_flow(watch_dir, "nav_b.flow", FORK_JOIN_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    wait_for_flow_discovery(base_url, "fork_join_test")

    page.goto(base_url)

    # Click the first flow
    page.locator('[data-testid="sidebar-flow-linear_test"]').click()

    # Should see linear flow nodes
    expect(page.locator('[data-testid="node-start"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-work"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-done"]')).to_be_visible(timeout=5000)

    # Click the second flow
    page.locator('[data-testid="sidebar-flow-fork_join_test"]').click()

    # Should see fork-join flow nodes instead
    expect(page.locator('[data-testid="node-analyze"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-test_unit"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-test_integration"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-report"]')).to_be_visible(timeout=5000)


def test_active_run_in_sidebar(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify an active run appears in the sidebar's ACTIVE RUNS section."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    gate = mock_subprocess.add_gate("work")
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "nav_run.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    page.goto(base_url)

    # The active run should appear in the sidebar
    run_entry = page.locator(f'[data-testid="sidebar-run-{run_id}"]')
    expect(run_entry).to_be_visible(timeout=10000)

    # Release gate to let flow finish
    gate.set()


def test_click_active_run_opens_detail(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify clicking an active run in sidebar opens Run Detail."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    gate = mock_subprocess.add_gate("work")
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "nav_run.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    page.goto(base_url)

    # Click the active run in sidebar
    run_entry = page.locator(f'[data-testid="sidebar-run-{run_id}"]')
    expect(run_entry).to_be_visible(timeout=10000)
    run_entry.click()

    # Wait for navigation to the run detail URL
    page.wait_for_url(f"**/runs/{run_id}", timeout=5000)

    # Wait for the run detail page to finish loading and render the graph
    expect(page.locator(".run-detail-header")).to_be_visible(timeout=10000)
    expect(page.locator('[data-testid="node-start"]')).to_be_visible(timeout=10000)

    # Release gate
    gate.set()
