"""Smoke test to verify E2E infrastructure works.

This minimal test verifies the server starts and responds to API calls.
It's the first test to run and validates the fixture hierarchy.
"""

from __future__ import annotations

import httpx

from tests.e2e.mock_subprocess import MockSubprocessManager


def test_server_is_reachable(base_url: str):
    """Verify the test server is running and responds to GET /api/flows."""
    resp = httpx.get(f"{base_url}/api/flows", timeout=5)
    assert resp.status_code == 200


def test_mock_subprocess_available(mock_subprocess: MockSubprocessManager):
    """Verify the mock subprocess manager fixture is injected."""
    assert isinstance(mock_subprocess, MockSubprocessManager)


def test_reset_endpoint(base_url: str):
    """Verify the test-only reset endpoint is available (FLOWSTATE_TEST_MODE=1)."""
    resp = httpx.post(f"{base_url}/api/_test/reset", timeout=5)
    assert resp.status_code == 200
