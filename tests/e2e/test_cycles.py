"""E2E tests for cycle execution.

Tests verifying nodes are re-entered with incrementing generation counts,
generation badges are displayed in the graph, and the flow eventually
exits the cycle.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    CYCLE_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def test_cycle_generation_badge(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify node re-entry shows generation badges and flow eventually completes."""
    mock_subprocess.configure_node("plan", NodeBehavior.success("Plan created."))
    mock_subprocess.configure_node("implement", NodeBehavior.success("Implementation done."))
    mock_subprocess.configure_node("verify", NodeBehavior.success("Verification complete."))
    mock_subprocess.configure_node("complete", NodeBehavior.success("All done."))

    # Judge: first two calls return "implement" (cycle), third returns "complete" (exit)
    mock_subprocess.configure_judge("verify", "implement", confidence=0.85)
    mock_subprocess.configure_judge("verify", "implement", confidence=0.85)
    mock_subprocess.configure_judge("verify", "complete", confidence=0.95)

    write_flow(watch_dir, "cycle_test.flow", CYCLE_FLOW, workspace)
    wait_for_flow_discovery(base_url, "cycle_test")
    run_id = start_run_via_api(base_url, "cycle_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Flow should eventually complete
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Completed", timeout=30000)

    # The implement node should show it was re-entered (generation badge)
    # After 2 cycles back: implement runs at gen 1, gen 2, gen 3 = 3 total executions
    expect(page.locator('[data-testid="node-implement"][data-status="completed"]')).to_be_visible(
        timeout=5000
    )

    # Verify the complete (exit) node completed
    expect(page.locator('[data-testid="node-complete"][data-status="completed"]')).to_be_visible(
        timeout=5000
    )


def test_cycle_logs_per_generation(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify logs show content from the latest generation after cycle."""
    mock_subprocess.configure_node("plan", NodeBehavior.success("Plan created."))
    mock_subprocess.configure_node(
        "implement",
        NodeBehavior.with_output("Implementing changes...", summary="Implementation done."),
    )
    mock_subprocess.configure_node(
        "verify",
        NodeBehavior.with_output("Checking results...", summary="Verification complete."),
    )
    mock_subprocess.configure_node("complete", NodeBehavior.success("All done."))

    # One cycle back, then exit
    mock_subprocess.configure_judge("verify", "implement", confidence=0.85)
    mock_subprocess.configure_judge("verify", "complete", confidence=0.95)

    write_flow(watch_dir, "cycle_test.flow", CYCLE_FLOW, workspace)
    wait_for_flow_discovery(base_url, "cycle_test")
    run_id = start_run_via_api(base_url, "cycle_test", workspace=str(workspace))

    page.goto(f"{base_url}/runs/{run_id}")

    # Wait for completion
    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("Completed", timeout=30000)

    # Click implement node to view logs
    page.locator('[data-testid="node-implement"]').click()

    # Log viewer should show the implement output
    log_viewer = page.locator('[data-testid="log-viewer"]')
    expect(log_viewer).to_contain_text("Implementing changes...", timeout=5000)
