"""Unit tests for ``flowstate init`` (SERVER-028).

These tests exercise the CLI command directly via ``typer.testing.CliRunner``
with a monkeypatched working directory pointing at ``tmp_path``. The real
``importlib.resources`` lookup is used — the templates ship as package data
and must be reachable from a source checkout. Each rendered template is also
fed through the real DSL parser + type checker to prove it is a valid flow
(no point shipping a "starter" that fails ``flowstate check`` out of the box).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from flowstate.cli import app
from flowstate.dsl.parser import parse_flow
from flowstate.dsl.type_checker import check_flow

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty scratch directory used as the CWD for ``flowstate init``."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)
    return tmp_path


def _assert_rendered_flow_typechecks(example_path: Path) -> None:
    """Parse and type-check a rendered example flow, failing loud on any error."""
    source = example_path.read_text()
    ast = parse_flow(source)
    errors = check_flow(ast)
    assert not errors, f"seeded example failed type-check: {errors}"


class TestFlowstateInitFresh:
    """`flowstate init` on an empty directory picks the generic template."""

    def test_creates_toml_and_example_generic(self, scratch: Path) -> None:
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stderr
        toml_path = scratch / "flowstate.toml"
        example_path = scratch / "flows" / "example.flow"
        assert toml_path.exists()
        assert example_path.exists()

        toml_content = toml_path.read_text()
        assert 'host = "127.0.0.1"' in toml_content
        assert "port = 9090" in toml_content
        assert 'watch_dir = "flows"' in toml_content

        example_content = example_path.read_text()
        # The generic template must mention "get started" per issue spec.
        assert "get started" in example_content
        _assert_rendered_flow_typechecks(example_path)

        assert "Next:" in result.stdout
        assert "flowstate check flows/example.flow" in result.stdout
        assert "flowstate server" in result.stdout

    def test_node_project_gets_node_template(self, scratch: Path) -> None:
        (scratch / "package.json").write_text('{"name": "demo"}\n')
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stderr
        example_content = (scratch / "flows" / "example.flow").read_text()
        assert "npm" in example_content
        _assert_rendered_flow_typechecks(scratch / "flows" / "example.flow")

    def test_python_project_gets_python_template(self, scratch: Path) -> None:
        (scratch / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stderr
        example_content = (scratch / "flows" / "example.flow").read_text()
        # Python template must reference a Python-specific tool.
        assert ("uv" in example_content) or ("pip" in example_content)
        _assert_rendered_flow_typechecks(scratch / "flows" / "example.flow")

    def test_rust_project_gets_rust_template(self, scratch: Path) -> None:
        (scratch / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.stderr
        example_content = (scratch / "flows" / "example.flow").read_text()
        assert "cargo" in example_content
        _assert_rendered_flow_typechecks(scratch / "flows" / "example.flow")


class TestFlowstateInitForce:
    """`flowstate init --force` overwrites toml but preserves user flows."""

    def test_pre_existing_toml_without_force_exits_1(self, scratch: Path) -> None:
        (scratch / "flowstate.toml").write_text("# user-edited marker line\n")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1
        assert "flowstate.toml already exists" in result.stderr
        assert "--force" in result.stderr
        # File must be unchanged.
        assert (scratch / "flowstate.toml").read_text() == "# user-edited marker line\n"

    def test_force_overwrites_toml_but_preserves_example_flow(self, scratch: Path) -> None:
        # Arrange: pre-existing toml with a marker, pre-existing user flow.
        (scratch / "flowstate.toml").write_text("# user-edited marker line\n")
        flows_dir = scratch / "flows"
        flows_dir.mkdir()
        example_path = flows_dir / "example.flow"
        example_path.write_text("// user's hand-edited flow\n")
        # Force an ancient mtime so we can detect accidental rewrites.
        ancient = 1_600_000_000
        os.utime(example_path, (ancient, ancient))

        result = runner.invoke(app, ["init", "--force"])
        assert result.exit_code == 0, result.stderr

        # Toml was rewritten — the marker is gone.
        new_toml = (scratch / "flowstate.toml").read_text()
        assert "user-edited marker" not in new_toml
        assert 'host = "127.0.0.1"' in new_toml

        # Example flow preserved — mtime unchanged.
        assert example_path.read_text() == "// user's hand-edited flow\n"
        assert int(os.path.getmtime(example_path)) == ancient

        # Output must explicitly note the preserved example file.
        assert "not overwriting" in result.stdout

    def test_force_without_existing_toml_still_works(self, scratch: Path) -> None:
        result = runner.invoke(app, ["init", "--force"])
        assert result.exit_code == 0, result.stderr
        assert (scratch / "flowstate.toml").exists()
