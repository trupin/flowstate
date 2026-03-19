"""E2E tests for the file watcher.

Tests verifying the UI auto-updates when .flow files change on disk, error
banners appear/disappear correctly, and new files are discovered.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    FORK_JOIN_FLOW,
    INVALID_FLOW,
    LINEAR_FLOW,
    wait_for_flow_discovery,
    wait_for_flow_gone,
    wait_for_flow_status_by_id,
    write_flow,
)


def test_modify_valid_to_invalid(page: Page, base_url: str, watch_dir, workspace):
    """Verify modifying a valid flow to invalid shows an error banner."""
    write_flow(watch_dir, "watcher_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")

    page.goto(base_url)
    flow_entry = page.locator('[data-testid="sidebar-flow-linear_test"]')
    expect(flow_entry).to_be_visible(timeout=5000)
    expect(flow_entry).to_have_attribute("data-status", "valid")

    # Click on the flow to select it (navigates to ?flow=watcher_test)
    flow_entry.click()

    # Overwrite with invalid content.  The flow's parsed name becomes None
    # so we wait by file-stem ID rather than by name.
    write_flow(watch_dir, "watcher_test.flow", INVALID_FLOW, workspace)
    wait_for_flow_status_by_id(base_url, "watcher_test", "error", timeout=10)

    # Reload the page with the same flow selected — the FlowLibrary will
    # fetch the now-invalid flow and display its error banner.
    page.goto(f"{base_url}/?flow=watcher_test")

    error_banner = page.locator('[data-testid="error-banner"]')
    expect(error_banner).to_be_visible(timeout=10000)


def test_fix_invalid_to_valid(page: Page, base_url: str, watch_dir, workspace):
    """Verify fixing an invalid flow to valid removes the error banner."""
    write_flow(watch_dir, "watcher_test.flow", INVALID_FLOW, workspace)
    # Wait for the file watcher to discover the invalid flow by ID
    wait_for_flow_status_by_id(base_url, "watcher_test", "error", timeout=10)

    # Navigate to the invalid flow — error banner should appear
    page.goto(f"{base_url}/?flow=watcher_test")
    error_banner = page.locator('[data-testid="error-banner"]')
    expect(error_banner).to_be_visible(timeout=10000)

    # Overwrite with valid content
    write_flow(watch_dir, "watcher_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")

    # Refresh to see the valid state
    page.goto(f"{base_url}/?flow=watcher_test")
    flow_entry = page.locator('[data-testid="sidebar-flow-linear_test"]')
    expect(flow_entry).to_be_visible(timeout=10000)
    expect(flow_entry).to_have_attribute("data-status", "valid")

    # Error banner should no longer be visible
    expect(error_banner).not_to_be_visible(timeout=5000)


def test_add_new_flow(page: Page, base_url: str, watch_dir, workspace):
    """Verify adding a new .flow file updates the sidebar."""
    write_flow(watch_dir, "first.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")

    page.goto(base_url)
    expect(page.locator('[data-testid="sidebar-flow-linear_test"]')).to_be_visible(timeout=5000)

    # Write a second flow (different template → different parsed name)
    write_flow(watch_dir, "second.flow", FORK_JOIN_FLOW, workspace)
    wait_for_flow_discovery(base_url, "fork_join_test")

    # Both should be visible in the sidebar
    expect(page.locator('[data-testid="sidebar-flow-linear_test"]')).to_be_visible(timeout=5000)
    expect(page.locator('[data-testid="sidebar-flow-fork_join_test"]')).to_be_visible(timeout=10000)


def test_delete_flow(page: Page, base_url: str, watch_dir, workspace):
    """Verify deleting a .flow file removes it from the sidebar."""
    flow_path = write_flow(watch_dir, "delete_me.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")

    page.goto(base_url)
    expect(page.locator('[data-testid="sidebar-flow-linear_test"]')).to_be_visible(timeout=5000)

    # Delete the file
    flow_path.unlink()
    wait_for_flow_gone(base_url, "linear_test", timeout=10)

    # Refresh the page and verify the flow is gone from the sidebar
    page.goto(base_url)
    expect(page.locator('[data-testid="sidebar-flow-linear_test"]')).not_to_be_visible(timeout=5000)
