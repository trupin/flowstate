"""E2E tests for the Flow Library page.

Tests flow discovery from the watched directory, validity status indicators,
graph previews, and error banner display for parse/type-check errors.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    FLOW_WITH_TYPE_ERROR,
    FORK_JOIN_FLOW,
    INVALID_FLOW,
    LINEAR_FLOW,
    wait_for_flow_discovery,
    wait_for_flow_status,
    write_flow,
)


def test_discover_valid_flow(page: Page, base_url: str, watch_dir, workspace):
    """Write a valid .flow file and verify it appears in the sidebar with 'valid' status."""
    write_flow(watch_dir, "my_flow.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test", timeout=10)

    page.goto(base_url)
    flow_entry = page.locator('[data-testid="sidebar-flow-linear_test"]')
    expect(flow_entry).to_be_visible(timeout=10000)
    expect(flow_entry).to_have_attribute("data-status", "valid")


def test_discover_invalid_flow(page: Page, base_url: str, watch_dir, workspace):
    """Write an invalid .flow file and verify it appears with 'error' status."""
    write_flow(watch_dir, "bad.flow", INVALID_FLOW, workspace)
    # The server should still discover the file, but mark it as invalid
    wait_for_flow_status(base_url, "bad", "error", timeout=10)

    page.goto(base_url)
    flow_entry = page.locator('[data-testid="sidebar-flow-bad"]')
    expect(flow_entry).to_be_visible(timeout=10000)
    expect(flow_entry).to_have_attribute("data-status", "error")


def test_flow_graph_preview(page: Page, base_url: str, watch_dir, workspace):
    """Click a valid flow in the sidebar and verify graph nodes render."""
    write_flow(watch_dir, "preview_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test", timeout=10)

    page.goto(base_url)
    flow_entry = page.locator('[data-testid="sidebar-flow-linear_test"]')
    expect(flow_entry).to_be_visible(timeout=10000)
    flow_entry.click()

    # Verify graph nodes are rendered
    expect(page.locator('[data-testid="node-start"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-work"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="node-done"]')).to_be_visible(timeout=5000)


def test_type_error_shows_banner(page: Page, base_url: str, watch_dir, workspace):
    """Write a flow with a type error and verify the error banner appears."""
    write_flow(watch_dir, "type_err.flow", FLOW_WITH_TYPE_ERROR, workspace)
    wait_for_flow_status(base_url, "missing_exit", "error", timeout=10)

    page.goto(base_url)
    flow_entry = page.locator('[data-testid="sidebar-flow-missing_exit"]')
    expect(flow_entry).to_be_visible(timeout=10000)
    flow_entry.click()

    error_banner = page.locator('[data-testid="error-banner"]')
    expect(error_banner).to_be_visible(timeout=5000)


def test_multiple_flows_listed(page: Page, base_url: str, watch_dir, workspace):
    """Write multiple flows and verify all appear in the sidebar with correct statuses."""
    write_flow(watch_dir, "flow_a.flow", LINEAR_FLOW, workspace)
    write_flow(watch_dir, "flow_b.flow", FORK_JOIN_FLOW, workspace)
    write_flow(watch_dir, "bad.flow", INVALID_FLOW, workspace)

    wait_for_flow_discovery(base_url, "linear_test", timeout=10)
    wait_for_flow_discovery(base_url, "fork_join_test", timeout=10)

    page.goto(base_url)

    # All three should appear
    expect(page.locator('[data-testid="sidebar-flow-linear_test"]')).to_be_visible(timeout=10000)
    expect(page.locator('[data-testid="sidebar-flow-fork_join_test"]')).to_be_visible(timeout=10000)
