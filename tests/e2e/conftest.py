"""E2E test fixture hierarchy.

Scoping strategy:
- Session: one Chromium browser instance (expensive to start ~3s)
- Module: one uvicorn server + mock subprocess manager + isolated DB/watch_dir per test file
- Test: fresh Playwright page + state reset (mock config, DB, watch_dir)
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
from pathlib import Path

import httpx
import pytest
import uvicorn

from tests.e2e.mock_subprocess import MockSubprocessManager

# Set test mode before any app imports
os.environ["FLOWSTATE_TEST_MODE"] = "1"

from flowstate.config import FlowstateConfig  # noqa: E402
from flowstate.server.app import create_app  # noqa: E402


def _find_free_port() -> int:
    """Find an available TCP port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 10.0) -> None:
    """Poll the server until it responds or timeout."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/api/flows", timeout=1)
            if resp.status_code in (200, 404):
                return
        except httpx.RequestError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Server on port {port} did not start within {timeout}s")


# ---------------------------------------------------------------------------
# Session-scoped: one Chromium browser for the entire test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def browser():
    """Launch a headless Chromium browser for the test session."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    b = pw.chromium.launch(headless=True)
    yield b
    b.close()
    pw.stop()


# ---------------------------------------------------------------------------
# Module-scoped: server + mock + isolated state per test file
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server_and_mock(tmp_path_factory):
    """Start a uvicorn server with MockSubprocessManager in a background thread.

    Each test module gets its own:
    - MockSubprocessManager instance
    - Unique port
    - Temp database directory
    - Temp watch directory
    """
    mock = MockSubprocessManager()
    port = _find_free_port()
    data_dir = tmp_path_factory.mktemp("data")
    watch_dir = tmp_path_factory.mktemp("flows")

    config = FlowstateConfig(
        server_port=port,
        database_path=str(data_dir / "flowstate.db"),
        watch_dir=str(watch_dir),
    )

    app = create_app(config=config, subprocess_manager=mock)

    uv_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(uv_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        _wait_for_server(port)
    except RuntimeError:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.skip(f"Could not start test server on port {port}")

    yield mock, f"http://localhost:{port}", Path(watch_dir)

    server.should_exit = True
    thread.join(timeout=10)


# Convenience fixtures that unpack server_and_mock


@pytest.fixture(scope="module")
def mock_subprocess(server_and_mock) -> MockSubprocessManager:
    """The MockSubprocessManager for this test module."""
    return server_and_mock[0]


@pytest.fixture(scope="module")
def base_url(server_and_mock) -> str:
    """The base URL of the test server for this module."""
    return server_and_mock[1]


@pytest.fixture(scope="module")
def watch_dir(server_and_mock) -> Path:
    """The watch directory for this test module."""
    return server_and_mock[2]


# ---------------------------------------------------------------------------
# Test-scoped: fresh page + state reset
# ---------------------------------------------------------------------------


@pytest.fixture()
def page(browser, base_url):
    """Create a fresh Playwright page for each test.

    Navigates to the base URL and provides a clean DOM context.
    """
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    p = ctx.new_page()
    p.goto(base_url)
    yield p
    p.close()
    ctx.close()


@pytest.fixture()
def context(browser, base_url):
    """Create a BrowserContext (for tests that need context-level APIs like set_offline)."""
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    yield ctx
    ctx.close()


@pytest.fixture()
def workspace(tmp_path) -> Path:
    """A temporary workspace directory for flow execution."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture(autouse=True)
def reset_state(mock_subprocess, watch_dir, base_url):
    """Reset all state between tests.

    - Clears mock subprocess configuration
    - Removes all .flow files from watch_dir
    - Truncates all DB tables via test-only endpoint
    """
    mock_subprocess.reset()

    for f in watch_dir.glob("*.flow"):
        f.unlink()

    with contextlib.suppress(httpx.RequestError):
        httpx.post(f"{base_url}/api/_test/reset", timeout=5)

    yield
