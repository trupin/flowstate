"""Tests for FastAPI app factory and config loading."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from flowstate.config import FlowstateConfig, load_config
from flowstate.server.app import FlowstateError, create_app

# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    def test_all_defaults(self) -> None:
        """FlowstateConfig() has all expected defaults."""
        config = FlowstateConfig()
        assert config.server_host == "127.0.0.1"
        assert config.server_port == 8080
        assert config.max_concurrent_tasks == 4
        assert config.default_budget == "1h"
        assert config.judge_model == "sonnet"
        assert config.judge_confidence_threshold == 0.5
        assert config.judge_max_retries == 1
        assert config.database_path == "~/.flowstate/flowstate.db"
        assert config.database_wal_mode is True
        assert config.watch_dir == "./flows"
        assert config.log_level == "info"


class TestLoadConfigFromFile:
    def test_partial_toml(self, tmp_path: Path) -> None:
        """Partial TOML file: overridden fields are loaded, missing fields get defaults."""
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[server]\nhost = "0.0.0.0"\nport = 9090\n\n[logging]\nlevel = "debug"\n'
        )
        config = load_config(path=str(toml_file))
        assert config.server_host == "0.0.0.0"
        assert config.server_port == 9090
        assert config.log_level == "debug"
        # Missing fields have defaults
        assert config.max_concurrent_tasks == 4
        assert config.default_budget == "1h"
        assert config.judge_model == "sonnet"
        assert config.database_path == "~/.flowstate/flowstate.db"

    def test_full_toml(self, tmp_path: Path) -> None:
        """Complete TOML with all sections -- every field is loaded."""
        toml_file = tmp_path / "full.toml"
        toml_file.write_text(
            "[server]\n"
            'host = "0.0.0.0"\n'
            "port = 3000\n"
            "\n"
            "[execution]\n"
            "max_concurrent_tasks = 8\n"
            'default_budget = "2h"\n'
            "\n"
            "[judge]\n"
            'model = "opus"\n'
            "confidence_threshold = 0.8\n"
            "max_retries = 3\n"
            "\n"
            "[database]\n"
            'path = "/tmp/test.db"\n'
            "wal_mode = false\n"
            "\n"
            "[flows]\n"
            'watch_dir = "/opt/flows"\n'
            "\n"
            "[logging]\n"
            'level = "warning"\n'
        )
        config = load_config(path=str(toml_file))
        assert config.server_host == "0.0.0.0"
        assert config.server_port == 3000
        assert config.max_concurrent_tasks == 8
        assert config.default_budget == "2h"
        assert config.judge_model == "opus"
        assert config.judge_confidence_threshold == 0.8
        assert config.judge_max_retries == 3
        assert config.database_path == "/tmp/test.db"
        assert config.database_wal_mode is False
        assert config.watch_dir == "/opt/flows"
        assert config.log_level == "warning"


class TestLoadConfigMissingFile:
    def test_explicit_nonexistent_path_raises(self) -> None:
        """Explicit path to nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config(path="/nonexistent/config.toml")

    def test_no_config_file_returns_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No config file found returns all defaults."""
        monkeypatch.chdir(tmp_path)
        # Ensure no global config either
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
        config = load_config()
        assert config == FlowstateConfig()


class TestLoadConfigEmptyFile:
    def test_empty_toml_returns_defaults(self, tmp_path: Path) -> None:
        """An empty TOML file produces all defaults."""
        toml_file = tmp_path / "empty.toml"
        toml_file.write_text("")
        config = load_config(path=str(toml_file))
        assert config == FlowstateConfig()


class TestLoadConfigSearchOrder:
    def test_local_file_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """flowstate.toml in cwd is found and loaded."""
        monkeypatch.chdir(tmp_path)
        local_config = tmp_path / "flowstate.toml"
        local_config.write_text("[server]\nport = 7777\n")
        config = load_config()
        assert config.server_port == 7777

    def test_global_config_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """~/.flowstate/config.toml is found when no local file exists."""
        monkeypatch.chdir(tmp_path)
        fake_home = tmp_path / "fakehome"
        flowstate_dir = fake_home / ".flowstate"
        flowstate_dir.mkdir(parents=True)
        global_config = flowstate_dir / "config.toml"
        global_config.write_text("[server]\nport = 6666\n")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        config = load_config()
        assert config.server_port == 6666

    def test_local_overrides_global(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Local flowstate.toml takes priority over global config."""
        monkeypatch.chdir(tmp_path)

        # Create global config
        fake_home = tmp_path / "fakehome"
        flowstate_dir = fake_home / ".flowstate"
        flowstate_dir.mkdir(parents=True)
        global_config = flowstate_dir / "config.toml"
        global_config.write_text("[server]\nport = 6666\n")
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        # Create local config
        local_config = tmp_path / "flowstate.toml"
        local_config.write_text("[server]\nport = 5555\n")

        config = load_config()
        assert config.server_port == 5555


class TestLoadConfigUnknownKeys:
    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        """Unknown keys in the TOML file are silently ignored."""
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[server]\nhost = "0.0.0.0"\nunknown_key = "value"\n\n'
            '[unknown_section]\nfoo = "bar"\n'
        )
        config = load_config(path=str(toml_file))
        assert config.server_host == "0.0.0.0"
        # All other fields are defaults
        assert config.server_port == 8080


# ---------------------------------------------------------------------------
# App factory tests
# ---------------------------------------------------------------------------


class TestCreateApp:
    def test_returns_fastapi_instance(self) -> None:
        """create_app() returns a FastAPI instance."""
        app = create_app()
        assert isinstance(app, FastAPI)

    def test_app_has_config(self) -> None:
        """App has a FlowstateConfig on app.state."""
        app = create_app()
        assert isinstance(app.state.config, FlowstateConfig)

    def test_app_has_cors_middleware(self) -> None:
        """App has CORSMiddleware configured."""
        app = create_app()
        assert any(m.cls is CORSMiddleware for m in app.user_middleware)

    def test_subprocess_manager_stored(self) -> None:
        """subprocess_manager argument is stored on app.state."""
        sentinel = object()
        app = create_app(subprocess_manager=sentinel)
        assert app.state.subprocess_manager is sentinel

    def test_subprocess_manager_none_by_default(self) -> None:
        """subprocess_manager is None by default."""
        app = create_app()
        assert app.state.subprocess_manager is None


class TestCreateAppWithCustomConfig:
    def test_custom_config_stored(self) -> None:
        """Custom config is stored on app.state."""
        config = FlowstateConfig(server_port=9090)
        app = create_app(config=config)
        assert app.state.config.server_port == 9090

    def test_custom_config_preserved(self) -> None:
        """All custom config fields are preserved."""
        config = FlowstateConfig(
            server_host="0.0.0.0",
            server_port=3000,
            log_level="debug",
        )
        app = create_app(config=config)
        assert app.state.config.server_host == "0.0.0.0"
        assert app.state.config.server_port == 3000
        assert app.state.config.log_level == "debug"


class TestErrorHandler:
    def test_flowstate_error_response(self) -> None:
        """FlowstateError is returned as JSON with error and details."""
        app = create_app()

        @app.get("/api/_test/error")
        async def raise_error() -> None:
            raise FlowstateError("test error", details=["detail1", "detail2"])

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/_test/error")
        assert response.status_code == 400
        body = response.json()
        assert body == {"error": "test error", "details": ["detail1", "detail2"]}

    def test_flowstate_error_custom_status_code(self) -> None:
        """FlowstateError respects custom status_code."""
        app = create_app()

        @app.get("/api/_test/notfound")
        async def raise_not_found() -> None:
            raise FlowstateError("not found", status_code=404)

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/_test/notfound")
        assert response.status_code == 404
        body = response.json()
        assert body == {"error": "not found", "details": []}

    def test_flowstate_error_empty_details(self) -> None:
        """FlowstateError with no details returns empty list."""
        app = create_app()

        @app.get("/api/_test/simple")
        async def raise_simple() -> None:
            raise FlowstateError("simple error")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/_test/simple")
        body = response.json()
        assert body["details"] == []


class TestCorsHeaders:
    def test_cors_preflight(self) -> None:
        """Preflight OPTIONS request from localhost gets CORS headers."""
        app = create_app()

        @app.get("/api/_test/cors")
        async def cors_test() -> dict[str, str]:
            return {"ok": "true"}

        client = TestClient(app)
        response = client.options(
            "/api/_test/cors",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_cors_simple_request(self) -> None:
        """Simple GET with Origin header gets CORS response headers."""
        app = create_app()

        @app.get("/api/_test/cors")
        async def cors_test() -> dict[str, str]:
            return {"ok": "true"}

        client = TestClient(app)
        response = client.get(
            "/api/_test/cors",
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_cors_non_localhost_rejected(self) -> None:
        """Non-localhost origin does not get CORS allow header."""
        app = create_app()

        @app.get("/api/_test/cors")
        async def cors_test() -> dict[str, str]:
            return {"ok": "true"}

        client = TestClient(app)
        response = client.get(
            "/api/_test/cors",
            headers={"Origin": "http://example.com"},
        )
        # Non-matching origin should not have the allow header
        assert response.headers.get("access-control-allow-origin") is None
