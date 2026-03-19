"""E2E tests for conditional branching.

Tests verifying the judge routes flow to the correct target based on mock
decisions. Tests both the "approved" path (to exit) and the "needs work"
path (cycle back).
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    CONDITIONAL_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def test_conditional_to_exit(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify judge decision routes to exit node when 'approved'."""
    mock_subprocess.configure_node("implement", NodeBehavior.success("Implementation done."))
    mock_subprocess.configure_node("review", NodeBehavior.success("Review complete."))
    mock_subprocess.configure_node("ship", NodeBehavior.success("Shipped."))

    # Judge decides "ship" (exit path)
    mock_subprocess.configure_judge("review", "ship", confidence=0.95)

    write_flow(watch_dir, "cond_test.flow", CONDITIONAL_FLOW, workspace)
    wait_for_flow_discovery(base_url, "conditional_test")
    run_id = start_run_via_api(base_url, "conditional_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Flow should reach ship (exit) and complete
    expect(page.locator('[data-testid="node-ship"][data-status="completed"]')).to_be_visible(
        timeout=20000
    )

    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Completed", timeout=20000)


def test_conditional_cycle_back(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify judge decision routes back to implement, then eventually to ship."""
    mock_subprocess.configure_node("implement", NodeBehavior.success("Implementation done."))
    mock_subprocess.configure_node("review", NodeBehavior.success("Review complete."))
    mock_subprocess.configure_node("ship", NodeBehavior.success("Shipped."))

    # First judge call: route back to implement (cycle)
    mock_subprocess.configure_judge("review", "implement", confidence=0.85)
    # Second judge call: route to ship (exit)
    mock_subprocess.configure_judge("review", "ship", confidence=0.95)

    write_flow(watch_dir, "cond_test.flow", CONDITIONAL_FLOW, workspace)
    wait_for_flow_discovery(base_url, "conditional_test")
    run_id = start_run_via_api(base_url, "conditional_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Flow should eventually complete via ship
    expect(page.locator('[data-testid="node-ship"][data-status="completed"]')).to_be_visible(
        timeout=30000
    )

    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Completed", timeout=30000)


def test_judge_decision_visible(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify the judge decision is reflected in the graph."""
    mock_subprocess.configure_node("implement", NodeBehavior.success("Implementation done."))
    mock_subprocess.configure_node("review", NodeBehavior.success("Review complete."))
    mock_subprocess.configure_node("ship", NodeBehavior.success("Shipped."))

    mock_subprocess.configure_judge("review", "ship", confidence=0.95)

    write_flow(watch_dir, "cond_test.flow", CONDITIONAL_FLOW, workspace)
    wait_for_flow_discovery(base_url, "conditional_test")
    run_id = start_run_via_api(base_url, "conditional_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for completion — the chosen path (review → ship) should be visible
    expect(page.locator('[data-testid="node-ship"][data-status="completed"]')).to_be_visible(
        timeout=20000
    )

    # All nodes in the flow should be visible in the graph
    expect(page.locator('[data-testid="node-implement"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-review"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-ship"]')).to_be_visible(timeout=5000)
