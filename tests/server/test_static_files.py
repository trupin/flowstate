"""Tests for static file serving and SPA fallback."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flowstate.server.app import create_app, mount_static_files

if TYPE_CHECKING:
    from tests.server.conftest import ProjectFixture


def _make_dist(tmp_path: Path, *, assets: bool = True, favicon: bool = False) -> Path:
    """Create a minimal ui/dist structure in tmp_path.

    Returns the dist directory path.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    index = dist / "index.html"
    index.write_text("<!doctype html><html><body>Flowstate App</body></html>")

    if assets:
        assets_dir = dist / "assets"
        assets_dir.mkdir()
        (assets_dir / "main.abc123.js").write_text("console.log('hello');")
        (assets_dir / "style.abc123.css").write_text("body { margin: 0; }")

    if favicon:
        (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00favicon-data")

    return dist


class TestStaticFilesServed:
    """Static files are served from the dist directory."""

    def test_root_returns_index_html(self, tmp_path: Path) -> None:
        """GET / returns index.html content with text/html content type."""
        dist = _make_dist(tmp_path)
        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/")
        assert response.status_code == 200
        assert "Flowstate App" in response.text
        assert "text/html" in response.headers["content-type"]

    def test_assets_js_served(self, tmp_path: Path) -> None:
        """GET /assets/main.abc123.js returns the JavaScript file."""
        dist = _make_dist(tmp_path)
        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/assets/main.abc123.js")
        assert response.status_code == 200
        assert "console.log" in response.text

    def test_assets_css_served(self, tmp_path: Path) -> None:
        """GET /assets/style.abc123.css returns the CSS file."""
        dist = _make_dist(tmp_path)
        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/assets/style.abc123.css")
        assert response.status_code == 200
        assert "body" in response.text


class TestSpaFallback:
    """Unknown paths return index.html for client-side routing."""

    def test_unknown_path_returns_index_html(self, tmp_path: Path) -> None:
        """GET /some/unknown/path returns index.html content, not 404."""
        dist = _make_dist(tmp_path)
        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/some/unknown/path")
        assert response.status_code == 200
        assert "Flowstate App" in response.text
        assert "text/html" in response.headers["content-type"]

    def test_nested_react_route(self, tmp_path: Path) -> None:
        """GET /flows/abc123/runs returns index.html for React Router."""
        dist = _make_dist(tmp_path)
        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/flows/abc123/runs")
        assert response.status_code == 200
        assert "Flowstate App" in response.text


class TestApiRoutesNotIntercepted:
    """API routes are not intercepted by the static file catch-all."""

    def test_api_route_takes_priority(self, tmp_path: Path) -> None:
        """GET /api/flows hits the API route, not the SPA fallback."""
        dist = _make_dist(tmp_path)
        app = FastAPI()

        # Register an API route BEFORE mounting static files
        @app.get("/api/flows")
        async def list_flows() -> dict[str, list[str]]:
            return {"flows": ["flow1", "flow2"]}

        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/api/flows")
        assert response.status_code == 200
        body = response.json()
        assert body == {"flows": ["flow1", "flow2"]}

    def test_api_nested_route_takes_priority(self, tmp_path: Path) -> None:
        """GET /api/runs/123 hits the API route, not the SPA fallback."""
        dist = _make_dist(tmp_path)
        app = FastAPI()

        @app.get("/api/runs/{run_id}")
        async def get_run(run_id: str) -> dict[str, str]:
            return {"id": run_id, "status": "running"}

        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/api/runs/123")
        assert response.status_code == 200
        body = response.json()
        assert body == {"id": "123", "status": "running"}


class TestNoDistDir:
    """App works normally when dist directory does not exist."""

    def test_no_crash_without_dist(self) -> None:
        """mount_static_files with nonexistent path does not raise."""
        app = FastAPI()
        mount_static_files(app, dist_dir=Path("/nonexistent/dist"))
        # App should still be functional — verify it has no SPA fallback
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/")
        # Without static files, root path returns 404 (no route defined)
        assert response.status_code == 404

    def test_warning_logged(self, caplog: logging.LogRecord) -> None:
        """A warning is logged when dist dir does not exist."""
        app = FastAPI()
        with caplog.at_level(logging.WARNING):  # type: ignore[union-attr]
            mount_static_files(app, dist_dir=Path("/nonexistent/dist"))
        assert any("not found" in record.message for record in caplog.records)  # type: ignore[union-attr]

    def test_api_routes_still_work(self) -> None:
        """API routes work normally when static files are not mounted."""
        app = FastAPI()
        mount_static_files(app, dist_dir=Path("/nonexistent/dist"))

        @app.get("/api/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app)
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestMissingIndexHtml:
    """Dist directory without index.html does not mount static files."""

    def test_no_mount_without_index(self, tmp_path: Path) -> None:
        """mount_static_files logs warning and skips when index.html is missing."""
        dist = tmp_path / "dist"
        dist.mkdir()
        # Create assets but no index.html
        assets_dir = dist / "assets"
        assets_dir.mkdir()
        (assets_dir / "main.js").write_text("console.log('hello');")

        app = FastAPI()
        mount_static_files(app, dist_dir=dist)

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/")
        # Without index.html, SPA fallback is not mounted
        assert response.status_code == 404

    def test_warning_logged_missing_index(self, tmp_path: Path, caplog: logging.LogRecord) -> None:
        """Warning is logged when dist exists but index.html does not."""
        dist = tmp_path / "dist"
        dist.mkdir()

        app = FastAPI()
        with caplog.at_level(logging.WARNING):  # type: ignore[union-attr]
            mount_static_files(app, dist_dir=dist)
        assert any("index.html" in record.message for record in caplog.records)  # type: ignore[union-attr]


class TestRealStaticFilePriority:
    """Real static files in dist/ are served with correct content."""

    def test_robots_txt_served(self, tmp_path: Path) -> None:
        """GET /robots.txt returns the file content, not index.html."""
        dist = _make_dist(tmp_path)
        robots = dist / "robots.txt"
        robots.write_text("User-agent: *\nDisallow: /api/")

        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/robots.txt")
        assert response.status_code == 200
        assert "User-agent" in response.text

    def test_manifest_json_served(self, tmp_path: Path) -> None:
        """GET /manifest.json returns the file content, not index.html."""
        dist = _make_dist(tmp_path)
        manifest = dist / "manifest.json"
        manifest.write_text('{"name": "Flowstate"}')

        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/manifest.json")
        assert response.status_code == 200
        assert "Flowstate" in response.text


class TestFavicon:
    """Favicon.ico is served correctly."""

    def test_favicon_served(self, tmp_path: Path) -> None:
        """GET /favicon.ico returns the favicon file when it exists."""
        dist = _make_dist(tmp_path, favicon=True)
        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/favicon.ico")
        assert response.status_code == 200
        assert b"favicon-data" in response.content

    def test_favicon_fallback_to_index(self, tmp_path: Path) -> None:
        """GET /favicon.ico falls back to index.html when favicon.ico does not exist."""
        dist = _make_dist(tmp_path, favicon=False)
        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/favicon.ico")
        assert response.status_code == 200
        # Returns index.html as fallback (FileResponse for index.html)


class TestCreateAppStaticDir:
    """create_app() integrates static file mounting via the static_dir parameter."""

    def test_static_dir_none_no_static_files(self, project_fixture: ProjectFixture) -> None:
        """create_app() with default static_dir=None does not mount static files."""
        app = create_app(project=project_fixture.project)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/")
        # No SPA fallback mounted, so root returns 404
        assert response.status_code == 404

    def test_static_dir_path_mounts_files(
        self, tmp_path: Path, project_fixture: ProjectFixture
    ) -> None:
        """create_app(static_dir=Path) mounts static files from that directory."""
        dist = _make_dist(tmp_path)
        app = create_app(project=project_fixture.project, static_dir=dist)
        client = TestClient(app)

        response = client.get("/")
        assert response.status_code == 200
        assert "Flowstate App" in response.text

    def test_static_dir_true_auto_detects(self, project_fixture: ProjectFixture) -> None:
        """create_app(static_dir=True) uses UI_DIST_DIR auto-detection."""
        # This test relies on the existence (or not) of the real ui/dist/ dir.
        # It should not crash either way.
        app = create_app(project=project_fixture.project, static_dir=True)
        assert isinstance(app, FastAPI)


class TestSpaFallbackApiGuard:
    """SPA fallback does not serve index.html for /api/* or /ws paths."""

    def test_api_path_returns_404_not_index(self, tmp_path: Path) -> None:
        """GET /api/nonexistent returns 404 JSON, not index.html."""
        dist = _make_dist(tmp_path)
        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/api/nonexistent")
        assert response.status_code == 404
        body = response.json()
        assert body["error"] == "Not found"

    def test_ws_path_returns_404_not_index(self, tmp_path: Path) -> None:
        """GET /ws returns 404 JSON, not index.html."""
        dist = _make_dist(tmp_path)
        app = FastAPI()
        mount_static_files(app, dist_dir=dist)
        client = TestClient(app)

        response = client.get("/ws")
        assert response.status_code == 404
        body = response.json()
        assert body["error"] == "Not found"
