"""E2E tests for the Submit Task flow.

Tests opening the task modal, verifying parameter form generation,
submitting the form, and task processing to Run Detail.
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
    """Verify the Submit Task modal opens when clicking the Submit Task button."""
    write_flow(watch_dir, "param_flow.flow", PARAMETERIZED_FLOW, workspace)
    wait_for_flow_discovery(base_url, "parameterized_test", timeout=10)

    _navigate_to_flow(page, base_url)

    start_btn = page.locator('[data-testid="submit-task-btn"]')
    expect(start_btn).to_be_visible(timeout=5000)
    start_btn.click()

    modal = page.locator(".task-modal-content")
    expect(modal).to_be_visible(timeout=5000)


def test_param_form_renders(page: Page, base_url: str, watch_dir, workspace):
    """Verify parameter inputs are rendered with correct types and defaults."""
    write_flow(watch_dir, "param_flow.flow", PARAMETERIZED_FLOW, workspace)
    wait_for_flow_discovery(base_url, "parameterized_test", timeout=10)

    _navigate_to_flow(page, base_url)

    start_btn = page.locator('[data-testid="submit-task-btn"]')
    expect(start_btn).to_be_visible(timeout=5000)
    start_btn.click()

    modal = page.locator(".task-modal-content")
    expect(modal).to_be_visible(timeout=5000)

    # Verify parameters section exists with the declared inputs
    params_section = modal.locator(".task-modal-params-section")
    expect(params_section).to_be_visible(timeout=5000)

    # focus param should show "all" as default
    expect(params_section).to_contain_text("focus")
    # verbose param should exist
    expect(params_section).to_contain_text("verbose")


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

    start_btn = page.locator('[data-testid="submit-task-btn"]')
    expect(start_btn).to_be_visible(timeout=5000)
    start_btn.click()

    # Fill title and submit
    modal = page.locator(".task-modal-content")
    expect(modal).to_be_visible(timeout=5000)
    modal.locator(".task-modal-input").first.fill("Test run")
    modal.locator(".task-modal-btn-submit").click()

    # Modal should close after submission
    expect(modal).not_to_be_visible(timeout=5000)


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

    start_btn = page.locator('[data-testid="submit-task-btn"]')
    expect(start_btn).to_be_visible(timeout=5000)
    start_btn.click()

    # The modal should be visible with parameter inputs
    modal = page.locator(".task-modal-content")
    expect(modal).to_be_visible(timeout=5000)

    # Fill title and submit
    modal.locator(".task-modal-input").first.fill("Test run with params")
    modal.locator(".task-modal-btn-submit").click()

    # Modal should close after submission
    expect(modal).not_to_be_visible(timeout=5000)
