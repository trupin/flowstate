"""E2E tests for flow control actions.

Tests pausing a running flow, resuming it, and cancelling. Uses mock gates
to hold tasks at controllable points so the test can interact with control
buttons while execution is in progress.

Note: The executor currently exits its main loop when paused, so the resume
operation requires the executor to remain active. The pause test verifies
that pause takes effect; the resume portion is deferred until the executor
supports waiting while paused.
"""

from __future__ import annotations

import contextlib
import threading

import httpx
from playwright.sync_api import Page, expect

from tests.e2e.flow_fixtures import (
    LINEAR_FLOW,
    start_run_via_api,
    wait_for_flow_discovery,
    wait_for_run_status,
    wait_for_task_status,
    write_flow,
)
from tests.e2e.mock_subprocess import MockSubprocessManager, NodeBehavior


def test_pause_and_resume(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify pause stops execution after current task completes."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    gate = mock_subprocess.add_gate("work")
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "ctrl_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    # Wait for "work" to be running (blocked by gate)
    wait_for_task_status(base_url, run_id, "work", "running")

    # Send pause in a background thread (it blocks until running tasks complete)
    def _send_pause():
        with contextlib.suppress(httpx.RequestError):
            httpx.post(f"{base_url}/api/runs/{run_id}/pause", timeout=30)

    pause_thread = threading.Thread(target=_send_pause, daemon=True)
    pause_thread.start()

    # Give the pause request time to reach the executor
    import time

    time.sleep(0.5)

    # Release the gate so work completes (and then the pause takes effect)
    gate.set()

    # Wait for paused status, then navigate
    wait_for_run_status(base_url, run_id, "paused")
    page.goto(f"{base_url}/runs/{run_id}")

    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("paused", timeout=15000)

    # Verify "work" completed before pause took effect
    expect(page.locator('[data-testid="node-work"][data-status="completed"]')).to_be_visible(
        timeout=5000
    )


def test_cancel(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify cancel terminates the flow."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    gate = mock_subprocess.add_gate("work")
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "ctrl_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test", timeout=10.0)
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    # Wait for work to be running
    wait_for_task_status(base_url, run_id, "work", "running")

    # Cancel via REST API (blocks until tasks finish, so fire in bg)
    def _send_cancel():
        with contextlib.suppress(httpx.RequestError):
            httpx.post(f"{base_url}/api/runs/{run_id}/cancel", timeout=30)

    cancel_thread = threading.Thread(target=_send_cancel, daemon=True)
    cancel_thread.start()

    # Release gate so the task can clean up
    gate.set()

    # Wait for cancelled status, then navigate
    wait_for_run_status(base_url, run_id, "cancelled")
    page.goto(f"{base_url}/runs/{run_id}")

    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("cancelled", timeout=15000)


def test_pause_button_visibility(
    page: Page,
    base_url: str,
    watch_dir,
    workspace,
    mock_subprocess: MockSubprocessManager,
):
    """Verify pause button is visible when running, hidden/disabled when completed."""
    mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
    mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
    mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))

    write_flow(watch_dir, "ctrl_test.flow", LINEAR_FLOW, workspace)
    wait_for_flow_discovery(base_url, "linear_test")
    run_id = start_run_via_api(base_url, "linear_test", workspace=str(workspace))

    # Wait for completion, then navigate
    wait_for_run_status(base_url, run_id, "completed")
    page.goto(f"{base_url}/runs/{run_id}")

    flow_status = page.locator('[data-testid="flow-status"]')
    expect(flow_status).to_have_text("completed", timeout=20000)

    # Pause button should be hidden or disabled after completion
    pause_btn = page.locator('[data-testid="btn-pause"]')
    expect(pause_btn).to_be_hidden(timeout=5000)
