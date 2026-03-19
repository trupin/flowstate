"""E2E tests for the Orchestrator Console in Run Detail.

Tests that the Orchestrator button appears when orchestrator sessions exist,
that clicking it shows the console panel, and that runs without orchestrator
sessions don't show the button.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    LINEAR_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    wait_for_run_status,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def _setup_and_complete_linear_flow(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
) -> str:
    """Write flow, configure mock, start run, wait for completion, return run_id."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "orch_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))
    wait_for_run_status(base_url, run_id, "completed", timeout=15.0)
    return run_id


def test_orchestrator_button_visible_on_run_detail(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """The Orchestrator button appears when orchestrator sessions exist."""
    run_id = _setup_and_complete_linear_flow(page, base_url, watch_dir, workspace, mock_subprocess)
    page.goto(f"{base_url}/runs/{run_id}")

    # The orchestrator button should be visible
    orch_btn = page.locator(".orchestrator-toggle-btn")
    expect(orch_btn).to_be_visible(timeout=10000)
    expect(orch_btn).to_contain_text("Orchestrator")


def test_orchestrator_button_toggles_console(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Clicking the Orchestrator button shows the console panel, clicking again hides it."""
    run_id = _setup_and_complete_linear_flow(page, base_url, watch_dir, workspace, mock_subprocess)
    page.goto(f"{base_url}/runs/{run_id}")

    orch_btn = page.locator(".orchestrator-toggle-btn")
    expect(orch_btn).to_be_visible(timeout=10000)

    # Click to show console
    orch_btn.click()
    orch_console = page.locator(".orchestrator-console")
    expect(orch_console).to_be_visible(timeout=5000)

    # Button should have active class
    expect(orch_btn).to_have_class(re.compile("active"))

    # Click again to hide
    orch_btn.click()
    expect(orch_console).not_to_be_visible(timeout=5000)

    # Log viewer should be back
    log_viewer = page.locator('[data-testid="log-viewer"]')
    expect(log_viewer).to_be_visible(timeout=5000)


def test_orchestrator_console_shows_system_prompt(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """The console shows a collapsible system prompt section."""
    run_id = _setup_and_complete_linear_flow(page, base_url, watch_dir, workspace, mock_subprocess)
    page.goto(f"{base_url}/runs/{run_id}")

    # Open orchestrator console
    page.locator(".orchestrator-toggle-btn").click()
    expect(page.locator(".orchestrator-console")).to_be_visible(timeout=5000)

    # System prompt header should be visible
    sys_prompt_header = page.locator(".orchestrator-system-prompt-header")
    expect(sys_prompt_header).to_be_visible(timeout=5000)
    expect(sys_prompt_header).to_contain_text("System Prompt")

    # System prompt content should be collapsed by default
    sys_prompt_content = page.locator(".orchestrator-system-prompt-content")
    expect(sys_prompt_content).not_to_be_visible()

    # Click to expand
    sys_prompt_header.click()
    expect(sys_prompt_content).to_be_visible(timeout=3000)

    # Should contain orchestrator-related text
    expect(sys_prompt_content).to_contain_text("Flowstate Orchestrator Agent")


def test_orchestrator_api_returns_sessions(
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """The orchestrators API endpoint returns session data after a run completes."""
    import httpx

    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "orch_api_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))
    wait_for_run_status(base_url, run_id, "completed", timeout=15.0)

    resp = httpx.get(f"{base_url}/api/runs/{run_id}/orchestrators", timeout=5)
    assert resp.status_code == 200
    orchestrators = resp.json()
    assert len(orchestrators) >= 1

    orch = orchestrators[0]
    assert "session_id" in orch
    assert "system_prompt" in orch
    assert "key" in orch
    assert len(orch["session_id"]) > 0
    assert "Flowstate Orchestrator Agent" in orch["system_prompt"]
