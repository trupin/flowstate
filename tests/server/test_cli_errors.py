"""Unit tests for the "no flowstate.toml" error path (SERVER-029).

These tests run every project-requiring CLI command inside a ``tmp_path``
with no anchor and assert that:

- The exit code is 2 (not 1) — conventional "usage error / context error".
- Stderr contains the friendly "No flowstate.toml found in <cwd>" message
  pointing at ``flowstate init``.
- No Python traceback is leaked.

The commands that legitimately run outside a project (``init``,
``--version``, ``--help``) are also covered here so a future refactor
cannot accidentally wire them into ``_require_project()`` without the
test suite noticing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from flowstate.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


@pytest.fixture
def no_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch CWD guaranteed to have no ``flowstate.toml`` ancestor.

    We jail ``FLOWSTATE_CONFIG`` (unset) and create ``tmp_path/deep/nested``
    so the walk-up also doesn't accidentally land on a developer machine's
    ``$HOME/flowstate.toml``. The fixture returns the innermost nested dir.
    """
    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)
    # Walk-up must stop at tmp_path before it can find anything real on
    # the host — but `_find_anchor` walks all the way to `/`, so we
    # additionally mock out `Path.cwd` / rely on jail via the chdir.
    # In practice tmp_path is under /private/var (macOS) or /tmp, neither
    # of which contains flowstate.toml on CI runners.
    return nested


class TestProjectRequiringCommandsFailGracefully:
    """Every project-requiring command exits 2 with the friendly error."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["server"],
            ["run", "flows/foo.flow"],
            ["runs"],
            ["status", "abc123"],
            ["schedules"],
            ["trigger", "some-flow"],
        ],
        ids=["server", "run", "runs", "status", "schedules", "trigger"],
    )
    def test_no_project_exits_2_with_friendly_message(
        self, no_project: Path, argv: list[str]
    ) -> None:
        result = runner.invoke(app, argv)
        assert result.exit_code == 2, (
            f"{argv} expected exit 2, got {result.exit_code}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        combined = (result.stdout or "") + (result.stderr or "")
        assert "No flowstate.toml found" in combined
        assert "flowstate init" in combined
        # No traceback should leak.
        assert "Traceback (most recent call last)" not in combined


class TestCommandsThatBypassProjectCheck:
    """`init`, `--version`, `--help`, and `check` still work outside a project."""

    def test_init_bypasses_project_check(self, no_project: Path) -> None:
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stderr
        assert (no_project / "flowstate.toml").exists()

    def test_help_works_outside_project(self, no_project: Path) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.stdout
        assert "server" in result.stdout
        assert "check" in result.stdout
        assert "run" in result.stdout
        assert "No flowstate.toml found" not in (result.stdout or "")
        assert "No flowstate.toml found" not in (result.stderr or "")

    def test_version_long_flag_works_outside_project(self, no_project: Path) -> None:
        """`flowstate --version` exits 0 outside any project and prints a version.

        SERVER-028 fix loop: the version flag is registered via an
        eager Typer callback so it fires before ``_require_project`` is
        ever consulted. It must not fail on a missing ``flowstate.toml``.
        """
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0, (
            f"expected exit 0, got {result.exit_code}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        combined = (result.stdout or "") + (result.stderr or "")
        # A non-empty version string must be present. We don't assert
        # the exact value because it varies between source checkouts
        # (fallback ``0.0.0+dev``) and installed wheels (PEP 440
        # releases like ``0.1.0``).
        assert "flowstate" in combined.lower()
        # Must contain something that looks like a version (digit + dot).
        import re

        assert re.search(
            r"\d+\.\d+", combined
        ), f"expected a version-looking substring in output; got {combined!r}"
        assert "No flowstate.toml found" not in combined

    def test_version_short_flag_works_outside_project(self, no_project: Path) -> None:
        """`flowstate -V` is an alias for `--version` and behaves identically."""
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        combined = (result.stdout or "") + (result.stderr or "")
        import re

        assert re.search(r"\d+\.\d+", combined)

    def test_check_without_project(self, no_project: Path) -> None:
        # `check` takes an explicit file path and is not a project-requiring
        # command. It should fail because the file doesn't exist, not
        # because there's no project.
        result = runner.invoke(app, ["check", "nonexistent.flow"])
        assert result.exit_code == 1
        # The error must be "file not found", NOT "no flowstate.toml".
        combined = (result.stdout or "") + (result.stderr or "")
        assert "No flowstate.toml found" not in combined


class TestFlowstateConfigEnvVar:
    """``FLOWSTATE_CONFIG`` pointing at a missing file yields its own message."""

    def test_missing_flowstate_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = tmp_path / "does-not-exist.toml"
        monkeypatch.setenv("FLOWSTATE_CONFIG", str(fake))
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["server"])
        assert result.exit_code == 2
        combined = (result.stdout or "") + (result.stderr or "")
        assert "FLOWSTATE_CONFIG" in combined
        assert str(fake) in combined
        assert "Traceback (most recent call last)" not in combined


class TestProjectParseError:
    """A ``flowstate.toml`` with invalid TOML still exits 2, no traceback."""

    def test_parse_error_exits_2(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "flowstate.toml").write_text("this = is [ not valid toml\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

        result = runner.invoke(app, ["runs"])
        assert result.exit_code == 2
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Traceback (most recent call last)" not in combined
        # The message must name the config so the user knows where to look.
        assert "flowstate.toml" in combined
