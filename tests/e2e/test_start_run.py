"""E2E tests for the Start Run flow.

Tests opening the start run modal, verifying parameter form generation,
submitting the form, and navigation to Run Detail.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    PARAMETERIZED_FLOW,
    wait_for_flow_discovery,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def _navigate_to_flow(page: Page, base_url: str):
    """Navigate to base URL and wait for the parameterized flow sidebar entry to stabilize."""
    page.goto(base_url)
    sidebar_entry = page.locator('[data-testid="sidebar-flow-parameterized_test"]')
    expect(sidebar_entry).to_be_visible(timeout=10000)
    sidebar_entry.click()


def test_modal_opens(page: Page, base_url: str, watch_dir, workspace):
    """Verify the Start Run modal opens when clicking the Start Run button."""
    write_flow(watch_dir, "param_flow.flow", PARAMETERIZED_FLOW, workspace)
    wait_for_flow_discovery(base_url, "parameterized_test", timeout=10)

    _navigate_to_flow(page, base_url)

    start_btn = page.locator('[data-testid="start-run-btn"]')
    expect(start_btn).to_be_visible(timeout=5000)
    start_btn.click()

    modal = page.locator('[data-testid="start-run-modal"]')
    expect(modal).to_be_visible(timeout=5000)


def test_param_form_renders(page: Page, base_url: str, watch_dir, workspace):
    """Verify parameter inputs are rendered with correct types and defaults."""
    write_flow(watch_dir, "param_flow.flow", PARAMETERIZED_FLOW, workspace)
    wait_for_flow_discovery(base_url, "parameterized_test", timeout=10)

    _navigate_to_flow(page, base_url)

    start_btn = page.locator('[data-testid="start-run-btn"]')
    expect(start_btn).to_be_visible(timeout=5000)
    start_btn.click()

    # focus param: text input with default "all"
    focus_input = page.locator('[data-testid="param-focus"]')
    expect(focus_input).to_be_visible(timeout=5000)
    expect(focus_input).to_have_value("all")

    # verbose param: checkbox, unchecked by default
    verbose_input = page.locator('[data-testid="param-verbose"]')
    expect(verbose_input).to_be_visible(timeout=5000)
    expect(verbose_input).not_to_be_checked()


def test_start_run_navigates(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify submitting the form starts a run and navigates to Run Detail."""
    write_flow(watch_dir, "param_flow.flow", PARAMETERIZED_FLOW, workspace)
    wait_for_flow_discovery(base_url, "parameterized_test", timeout=10)

    # Configure all nodes to succeed
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    _navigate_to_flow(page, base_url)

    start_btn = page.locator('[data-testid="start-run-btn"]')
    expect(start_btn).to_be_visible(timeout=5000)
    start_btn.click()

    # Fill and submit
    expect(page.locator('[data-testid="start-run-modal"]')).to_be_visible(timeout=5000)
    page.locator('[data-testid="param-focus"]').fill("auth module")
    page.locator('[data-testid="start-run-modal"] button[type="submit"]').click()

    # Should navigate to Run Detail -- graph view should be visible
    expect(page.locator('[data-testid="node-start"]')).to_be_visible(timeout=15000)


def test_start_run_with_custom_params(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify starting a run with custom parameters works correctly."""
    write_flow(watch_dir, "param_flow.flow", PARAMETERIZED_FLOW, workspace)
    wait_for_flow_discovery(base_url, "parameterized_test", timeout=10)

    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    _navigate_to_flow(page, base_url)

    start_btn = page.locator('[data-testid="start-run-btn"]')
    expect(start_btn).to_be_visible(timeout=5000)
    start_btn.click()

    # Set custom params
    expect(page.locator('[data-testid="start-run-modal"]')).to_be_visible(timeout=5000)
    page.locator('[data-testid="param-focus"]').fill("auth")
    page.locator('[data-testid="param-verbose"]').check()
    page.locator('[data-testid="start-run-modal"] button[type="submit"]').click()

    # Flow should start executing -- first node should appear
    expect(page.locator('[data-testid="node-start"]')).to_be_visible(timeout=15000)
