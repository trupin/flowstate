"""Unit tests for the non-loopback bind warning (SERVER-030).

Two layers:

1. Pure-function tests for ``_warn_if_non_loopback`` — capture stderr and
   assert the banner is present/absent for every host variant. These are
   the fast, deterministic tests that drive the bulk of coverage.
2. One CLI-level integration test that exercises the ``flowstate server``
   command via ``CliRunner``, with ``uvicorn.run`` monkeypatched to a no-op
   so we don't actually bind a port. This catches regressions in the
   wiring order — e.g. if someone accidentally moves the warning below
   ``uvicorn.run`` or removes it entirely, the CLI test fires.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from flowstate.cli import LOOPBACK_HOSTS, _warn_if_non_loopback, app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


class TestWarnIfNonLoopbackPureFunction:
    """`_warn_if_non_loopback` is a pure function — test it directly."""

    def test_zero_zero_zero_zero_emits_banner(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            _warn_if_non_loopback("0.0.0.0", 9090)
        out = buf.getvalue()
        assert "WARNING" in out
        assert "NO AUTHENTICATION" in out
        assert "0.0.0.0:9090" in out
        assert "Only use non-loopback binds in trusted networks" in out
        # Multi-line banner with border rows.
        assert "=" * 60 in out

    def test_ipv6_wildcard_emits_banner(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            _warn_if_non_loopback("::", 8080)
        out = buf.getvalue()
        assert "WARNING" in out
        assert "::" in out
        assert "8080" in out

    def test_routable_host_emits_banner(self) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            _warn_if_non_loopback("192.168.1.10", 9090)
        out = buf.getvalue()
        assert "WARNING" in out
        assert "192.168.1.10:9090" in out

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_hosts_are_silent(self, host: str) -> None:
        buf = io.StringIO()
        with redirect_stderr(buf):
            _warn_if_non_loopback(host, 9090)
        assert buf.getvalue() == ""

    def test_loopback_set_is_exactly_three_hosts(self) -> None:
        # Regression guard: this set defines the security posture. Adding
        # another "safe" host should be a deliberate spec change, not an
        # accidental edit.
        assert frozenset({"127.0.0.1", "localhost", "::1"}) == LOOPBACK_HOSTS


@pytest.fixture
def seeded_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp CWD with a minimal flowstate.toml so ``server`` resolves a project."""
    (tmp_path / "flowstate.toml").write_text('[server]\nhost = "127.0.0.1"\nport = 9090\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)
    return tmp_path


class TestServerCommandWiring:
    """`flowstate server` must call the warning helper with the resolved host."""

    def test_default_host_does_not_warn(
        self, seeded_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Intercept uvicorn.run so no real bind happens.
        import uvicorn

        captured: dict[str, object] = {}

        def fake_run(application: object, host: str, port: int) -> None:
            captured["host"] = host
            captured["port"] = port

        monkeypatch.setattr(uvicorn, "run", fake_run)

        # Also intercept create_app so we don't spin up a real DB / watcher.
        import flowstate.server.app as app_mod

        def fake_create_app(**_: object) -> object:
            return object()

        monkeypatch.setattr(app_mod, "create_app", fake_create_app)

        result = runner.invoke(app, ["server"])
        assert result.exit_code == 0, result.stderr
        assert captured["host"] == "127.0.0.1"
        assert "WARNING" not in (result.stderr or "")

    def test_explicit_zero_zero_zero_zero_warns(
        self, seeded_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uvicorn

        def fake_run(application: object, host: str, port: int) -> None:
            pass

        monkeypatch.setattr(uvicorn, "run", fake_run)

        import flowstate.server.app as app_mod

        def fake_create_app(**_: object) -> object:
            return object()

        monkeypatch.setattr(app_mod, "create_app", fake_create_app)

        result = runner.invoke(app, ["server", "--host", "0.0.0.0", "--port", "9099"])
        assert result.exit_code == 0, result.stderr
        assert "WARNING" in result.stderr
        assert "0.0.0.0:9099" in result.stderr
        assert "NO AUTHENTICATION" in result.stderr

    def test_explicit_localhost_does_not_warn(
        self, seeded_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uvicorn

        def fake_run(application: object, host: str, port: int) -> None:
            pass

        monkeypatch.setattr(uvicorn, "run", fake_run)

        import flowstate.server.app as app_mod

        def fake_create_app(**_: object) -> object:
            return object()

        monkeypatch.setattr(app_mod, "create_app", fake_create_app)

        result = runner.invoke(app, ["server", "--host", "localhost"])
        assert result.exit_code == 0, result.stderr
        assert "WARNING" not in (result.stderr or "")
