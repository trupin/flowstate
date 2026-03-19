"""E2E tests for fork-join execution.

Tests verifying parallel tasks are shown simultaneously, both complete
independently, and the join node activates only after all fork members finish.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    FORK_JOIN_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def test_fork_both_targets_execute(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify both fork targets execute and join node completes after both."""
    mock_subprocess.configure_node("analyze", NodeBehavior.success("Analysis complete."))
    mock_subprocess.configure_node("test_unit", NodeBehavior.success("Unit tests pass."))
    mock_subprocess.configure_node(
        "test_integration", NodeBehavior.success("Integration tests pass.")
    )
    mock_subprocess.configure_node("report", NodeBehavior.success("Report written."))

    write_flow(watch_dir, "fj_test.flow", FORK_JOIN_FLOW, workspace)
    wait_for_flow_discovery(base_url, "fork_join_test")
    run_id = start_run_via_api(base_url, "fork_join_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Both fork targets should reach completed
    expect(page.locator('[data-testid="node-test_unit"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )
    expect(
        page.locator('[data-testid="node-test_integration"][data-status="completed"]')
    ).to_be_visible(timeout=15000)

    # Join node (report) should also complete
    expect(page.locator('[data-testid="node-report"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )

    # Flow should be completed
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Completed", timeout=20000)


def test_fork_join_ordering(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify join node only starts after both fork members complete."""
    mock_subprocess.configure_node("analyze", NodeBehavior.success("Analysis complete."))

    # Gate both fork targets to control completion order
    gate_unit = mock_subprocess.add_gate("test_unit")
    gate_integration = mock_subprocess.add_gate("test_integration")
    mock_subprocess.configure_node("test_unit", NodeBehavior.success("Unit tests pass."))
    mock_subprocess.configure_node(
        "test_integration", NodeBehavior.success("Integration tests pass.")
    )
    mock_subprocess.configure_node("report", NodeBehavior.success("Report written."))

    write_flow(watch_dir, "fj_test.flow", FORK_JOIN_FLOW, workspace)
    wait_for_flow_discovery(base_url, "fork_join_test")
    run_id = start_run_via_api(base_url, "fork_join_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for analyze to complete
    expect(page.locator('[data-testid="node-analyze"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )

    # Both fork targets should be running (gated)
    expect(page.locator('[data-testid="node-test_unit"][data-status="running"]')).to_be_visible(
        timeout=15000
    )
    expect(
        page.locator('[data-testid="node-test_integration"][data-status="running"]')
    ).to_be_visible(timeout=15000)

    # Report should NOT be running yet
    expect(page.locator('[data-testid="node-report"][data-status="running"]')).not_to_be_visible(
        timeout=2000
    )

    # Release unit tests first
    gate_unit.set()
    expect(page.locator('[data-testid="node-test_unit"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )

    # Report should still not be running (waiting for integration)
    expect(page.locator('[data-testid="node-report"][data-status="running"]')).not_to_be_visible(
        timeout=2000
    )

    # Release integration tests
    gate_integration.set()
    expect(
        page.locator('[data-testid="node-test_integration"][data-status="completed"]')
    ).to_be_visible(timeout=15000)

    # Now report should start and complete
    expect(page.locator('[data-testid="node-report"][data-status="completed"]')).to_be_visible(
        timeout=15000
    )


def test_fork_join_graph_structure(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify the graph shows all fork-join nodes correctly."""
    mock_subprocess.configure_node("analyze", NodeBehavior.success("Analysis complete."))
    mock_subprocess.configure_node("test_unit", NodeBehavior.success("Unit tests pass."))
    mock_subprocess.configure_node(
        "test_integration", NodeBehavior.success("Integration tests pass.")
    )
    mock_subprocess.configure_node("report", NodeBehavior.success("Report written."))

    write_flow(watch_dir, "fj_test.flow", FORK_JOIN_FLOW, workspace)
    wait_for_flow_discovery(base_url, "fork_join_test")
    run_id = start_run_via_api(base_url, "fork_join_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # All four nodes should be visible in the graph
    expect(page.locator('[data-testid="node-analyze"]')).to_be_visible(timeout=15000)
    expect(page.locator('[data-testid="node-test_unit"]')).to_be_visible(timeout=15000)
    expect(page.locator('[data-testid="node-test_integration"]')).to_be_visible(timeout=15000)
    expect(page.locator('[data-testid="node-report"]')).to_be_visible(timeout=15000)
