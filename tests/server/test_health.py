"""Integration tests for ``GET /health`` (SERVER-031).

These tests build a real FastAPI app via ``create_app(project=...)``
— the same construction path production uses — and hit ``/health`` via
``TestClient``. They deliberately do **not** start the full lifespan
(``TestClient`` runs it, but we only care about the synchronous handler
output) and do **not** probe any authenticated surface, mirroring how a
pipx-installed user would call the endpoint from ``curl`` without any
setup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from flowstate.server.app import create_app
from flowstate.server.health import _DEV_VERSION

if TYPE_CHECKING:
    import pytest

    from tests.server.conftest import ProjectFixture


class TestHealthEndpoint:
    """Shape, status, and project slug/root come from ``app.state.project``."""

    def test_health_returns_200_with_project_slug(self, project_fixture: ProjectFixture) -> None:
        app = create_app(project=project_fixture.project)
        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 200
        body = response.json()

        # Top-level shape — exactly three keys, nothing else (TEST-16).
        assert set(body.keys()) == {"status", "version", "project"}
        assert body["status"] == "ok"
        assert isinstance(body["version"], str)
        assert body["version"] != ""

        project_block = body["project"]
        assert set(project_block.keys()) == {"slug", "root"}
        # Slug is "<basename>-<sha1(abspath)[:8]>" — so it starts with "project-".
        assert project_block["slug"].startswith("project-")
        assert project_block["slug"] == project_fixture.project.slug
        # Root must be an absolute path matching the fixture's project root.
        assert project_block["root"] == str(project_fixture.project.root)

    def test_health_does_not_leak_internal_paths(self, project_fixture: ProjectFixture) -> None:
        """TEST-16 — the payload must not expose db_path or workspaces_dir."""
        app = create_app(project=project_fixture.project)
        with TestClient(app) as client:
            response = client.get("/health")

        body = response.json()
        # Stringified full body, so we catch any accidental nesting too.
        serialized = str(body)
        assert "flowstate.db" not in serialized
        assert "workspaces" not in serialized
        assert "data_dir" not in serialized

    def test_health_version_falls_back_on_package_not_found(
        self,
        project_fixture: ProjectFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Source checkouts should answer with ``"0.0.0+dev"`` without raising."""
        from importlib.metadata import PackageNotFoundError

        import flowstate.server.health as health_mod

        def boom(_name: str) -> str:
            raise PackageNotFoundError("flowstate")

        monkeypatch.setattr(health_mod, "pkg_version", boom)

        app = create_app(project=project_fixture.project)
        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["version"] == _DEV_VERSION

    def test_health_survives_multiple_requests(self, project_fixture: ProjectFixture) -> None:
        """Readiness endpoints are hit in tight polling loops — make sure
        nothing caches per-request state."""
        app = create_app(project=project_fixture.project)
        with TestClient(app) as client:
            for _ in range(5):
                response = client.get("/health")
                assert response.status_code == 200
                assert response.json()["status"] == "ok"
