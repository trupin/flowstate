"""Tests for Lumon sandboxing -- deploy, plugin management, and config resolution.

All tests mock subprocess calls so no real `lumon` binary is needed.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from flowstate.dsl.ast import ContextMode, ErrorPolicy, Flow, Node, NodeType
from flowstate.engine.lumon import (
    LumonDeployError,
    LumonNotInstalledError,
    _builtin_plugin_dir,
    _lumon_config,
    _use_lumon,
    setup_lumon,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_flow(
    *,
    lumon: bool = False,
    sandbox: bool = False,
    lumon_config: str | None = None,
    sandbox_policy: str | None = None,
) -> Flow:
    return Flow(
        name="test_flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace="/workspace",
        lumon=lumon,
        sandbox=sandbox,
        lumon_config=lumon_config,
        sandbox_policy=sandbox_policy,
    )


def _make_node(
    name: str = "task1",
    *,
    lumon: bool | None = None,
    sandbox: bool | None = None,
    lumon_config: str | None = None,
    sandbox_policy: str | None = None,
) -> Node:
    return Node(
        name=name,
        node_type=NodeType.TASK,
        prompt="Do the task",
        lumon=lumon,
        sandbox=sandbox,
        lumon_config=lumon_config,
        sandbox_policy=sandbox_policy,
    )


# ---------------------------------------------------------------------------
# _use_lumon tests
# ---------------------------------------------------------------------------


class TestUseLumon:
    """Test _use_lumon() with various flow/node combinations."""

    def test_both_false(self) -> None:
        flow = _make_flow()
        node = _make_node()
        assert _use_lumon(flow, node) is False

    def test_flow_lumon_true(self) -> None:
        flow = _make_flow(lumon=True)
        node = _make_node()
        assert _use_lumon(flow, node) is True

    def test_flow_sandbox_true(self) -> None:
        flow = _make_flow(sandbox=True)
        node = _make_node()
        assert _use_lumon(flow, node) is True

    def test_node_lumon_true(self) -> None:
        flow = _make_flow()
        node = _make_node(lumon=True)
        assert _use_lumon(flow, node) is True

    def test_node_sandbox_true(self) -> None:
        flow = _make_flow()
        node = _make_node(sandbox=True)
        assert _use_lumon(flow, node) is True

    def test_node_overrides_flow_lumon(self) -> None:
        """Node lumon=False overrides flow lumon=True."""
        flow = _make_flow(lumon=True)
        node = _make_node(lumon=False)
        assert _use_lumon(flow, node) is False

    def test_node_overrides_flow_sandbox(self) -> None:
        """Node sandbox=False overrides flow sandbox=True."""
        flow = _make_flow(sandbox=True)
        node = _make_node(sandbox=False)
        assert _use_lumon(flow, node) is False

    def test_node_none_inherits_flow(self) -> None:
        """Node lumon=None inherits flow lumon=True."""
        flow = _make_flow(lumon=True)
        node = _make_node(lumon=None)
        assert _use_lumon(flow, node) is True

    def test_flow_sandbox_true_node_lumon_none_sandbox_none(self) -> None:
        """Node inherits flow sandbox when both node flags are None."""
        flow = _make_flow(sandbox=True)
        node = _make_node(lumon=None, sandbox=None)
        assert _use_lumon(flow, node) is True

    def test_lumon_or_sandbox_either_suffices(self) -> None:
        """Either lumon or sandbox being true is sufficient."""
        flow = _make_flow(lumon=True, sandbox=False)
        node = _make_node()
        assert _use_lumon(flow, node) is True

        flow2 = _make_flow(lumon=False, sandbox=True)
        assert _use_lumon(flow2, node) is True

    def test_node_lumon_false_but_sandbox_true(self) -> None:
        """Node lumon=False but sandbox=True -> still active."""
        flow = _make_flow()
        node = _make_node(lumon=False, sandbox=True)
        assert _use_lumon(flow, node) is True


# ---------------------------------------------------------------------------
# _lumon_config tests
# ---------------------------------------------------------------------------


class TestLumonConfig:
    """Test _lumon_config() resolution priority."""

    def test_no_config_anywhere(self) -> None:
        flow = _make_flow()
        node = _make_node()
        assert _lumon_config(flow, node) is None

    def test_node_lumon_config(self) -> None:
        """Node lumon_config takes highest priority."""
        flow = _make_flow(lumon_config="flow.lumon.json", sandbox_policy="flow-policy.json")
        node = _make_node(lumon_config="node.lumon.json", sandbox_policy="node-policy.json")
        assert _lumon_config(flow, node) == "node.lumon.json"

    def test_node_sandbox_policy(self) -> None:
        """Node sandbox_policy is second priority."""
        flow = _make_flow(lumon_config="flow.lumon.json", sandbox_policy="flow-policy.json")
        node = _make_node(sandbox_policy="node-policy.json")
        assert _lumon_config(flow, node) == "node-policy.json"

    def test_flow_lumon_config(self) -> None:
        """Flow lumon_config is third priority."""
        flow = _make_flow(lumon_config="flow.lumon.json", sandbox_policy="flow-policy.json")
        node = _make_node()
        assert _lumon_config(flow, node) == "flow.lumon.json"

    def test_flow_sandbox_policy(self) -> None:
        """Flow sandbox_policy is fourth (lowest) priority."""
        flow = _make_flow(sandbox_policy="flow-policy.json")
        node = _make_node()
        assert _lumon_config(flow, node) == "flow-policy.json"

    def test_node_lumon_config_overrides_flow(self) -> None:
        """Node-level always wins over flow-level."""
        flow = _make_flow(lumon_config="flow.json")
        node = _make_node(lumon_config="node.json")
        assert _lumon_config(flow, node) == "node.json"


# ---------------------------------------------------------------------------
# setup_lumon tests
# ---------------------------------------------------------------------------


def _mock_successful_deploy() -> AsyncMock:
    """Create a mock for asyncio.create_subprocess_exec that simulates successful deploy."""
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"deployed", b""))
    return mock_proc


def _mock_failed_deploy(exit_code: int = 1, stderr: str = "deploy error") -> AsyncMock:
    """Create a mock for asyncio.create_subprocess_exec that simulates failed deploy."""
    mock_proc = AsyncMock()
    mock_proc.returncode = exit_code
    mock_proc.communicate = AsyncMock(return_value=(b"", stderr.encode()))
    return mock_proc


class TestSetupLumon:
    """Test setup_lumon() with mocked subprocess."""

    @pytest.fixture()
    def worktree(self, tmp_path: Path) -> Path:
        """Create a temporary worktree directory."""
        wt = tmp_path / "worktree"
        wt.mkdir()
        return wt

    @pytest.fixture()
    def flow_dir(self, tmp_path: Path) -> Path:
        """Create a temporary flow file directory."""
        fd = tmp_path / "flow_dir"
        fd.mkdir()
        return fd

    async def test_creates_plugins_dir(self, worktree: Path) -> None:
        """setup_lumon creates plugins/ directory."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        assert (worktree / "plugins").is_dir()

    async def test_creates_sandbox_dir(self, worktree: Path) -> None:
        """setup_lumon creates sandbox/ directory."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        assert (worktree / "sandbox").is_dir()

    async def test_runs_lumon_deploy(self, worktree: Path) -> None:
        """setup_lumon calls lumon deploy with correct args."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        mock_exec.assert_called_once_with(
            "lumon",
            "deploy",
            str(worktree),
            "--force",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_deploy_failure_raises(self, worktree: Path) -> None:
        """Failed lumon deploy raises LumonDeployError."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_failed_deploy(1, "permission denied")
            with pytest.raises(LumonDeployError, match="lumon deploy failed"):
                await setup_lumon(str(worktree), flow, node)

    async def test_lumon_not_installed_raises(self, worktree: Path) -> None:
        """Missing lumon binary raises LumonNotInstalledError."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = FileNotFoundError("No such file: lumon")
            with pytest.raises(LumonNotInstalledError, match="not installed"):
                await setup_lumon(str(worktree), flow, node)

    async def test_symlinks_global_plugins(self, worktree: Path, tmp_path: Path) -> None:
        """setup_lumon symlinks plugins from ~/.flowstate/plugins/."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        # Create fake global plugins dir
        global_plugins = tmp_path / "home" / ".flowstate" / "plugins"
        global_plugins.mkdir(parents=True)
        (global_plugins / "my_plugin").mkdir()
        (global_plugins / "my_plugin" / "manifest.lumon").write_text("{}")

        with (
            patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec,
            patch("flowstate.engine.lumon.Path.home", return_value=tmp_path / "home"),
        ):
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        plugins_dir = worktree / "plugins"
        assert (plugins_dir / "my_plugin").is_symlink()
        assert (plugins_dir / "my_plugin").resolve() == global_plugins / "my_plugin"

    async def test_symlinks_per_flow_plugins(
        self, worktree: Path, flow_dir: Path, tmp_path: Path
    ) -> None:
        """setup_lumon symlinks plugins from <flow_file_dir>/plugins/."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        # Create per-flow plugins
        flow_plugins = flow_dir / "plugins"
        flow_plugins.mkdir()
        (flow_plugins / "flow_plugin").mkdir()
        (flow_plugins / "flow_plugin" / "manifest.lumon").write_text("{}")

        with (
            patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec,
            patch("flowstate.engine.lumon.Path.home", return_value=tmp_path / "home"),
        ):
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node, flow_file_dir=str(flow_dir))

        plugins_dir = worktree / "plugins"
        assert (plugins_dir / "flow_plugin").is_symlink()
        assert (plugins_dir / "flow_plugin").resolve() == flow_plugins / "flow_plugin"

    async def test_per_flow_overrides_global(
        self, worktree: Path, flow_dir: Path, tmp_path: Path
    ) -> None:
        """Per-flow plugin overrides global plugin with same name."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        # Create global and per-flow plugin with same name
        home = tmp_path / "home"
        global_plugins = home / ".flowstate" / "plugins"
        global_plugins.mkdir(parents=True)
        (global_plugins / "shared_plugin").mkdir()
        (global_plugins / "shared_plugin" / "v1.txt").write_text("global")

        flow_plugins = flow_dir / "plugins"
        flow_plugins.mkdir()
        (flow_plugins / "shared_plugin").mkdir()
        (flow_plugins / "shared_plugin" / "v2.txt").write_text("per-flow")

        with (
            patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec,
            patch("flowstate.engine.lumon.Path.home", return_value=home),
        ):
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node, flow_file_dir=str(flow_dir))

        # Per-flow should win
        plugins_dir = worktree / "plugins"
        target = (plugins_dir / "shared_plugin").resolve()
        assert target == flow_plugins / "shared_plugin"

    async def test_symlinks_builtin_flowstate_plugin(self, worktree: Path, tmp_path: Path) -> None:
        """setup_lumon symlinks the built-in flowstate plugin."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        with (
            patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec,
            patch("flowstate.engine.lumon.Path.home", return_value=tmp_path / "home"),
        ):
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        plugins_dir = worktree / "plugins"
        assert (plugins_dir / "flowstate").is_symlink()
        assert (plugins_dir / "flowstate").resolve() == _builtin_plugin_dir().resolve()

    async def test_copies_lumon_config_merged_with_flowstate(
        self, worktree: Path, flow_dir: Path
    ) -> None:
        """setup_lumon merges user config with built-in flowstate plugin."""
        flow = _make_flow(lumon=True, lumon_config="custom.lumon.json")
        node = _make_node()

        # Create user config with a custom plugin
        config_file = flow_dir / "custom.lumon.json"
        config_file.write_text('{"plugins": {"browser": {"expose": ["navigate"]}}}')

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node, flow_file_dir=str(flow_dir))

        assert (worktree / ".lumon.json").exists()
        config = json.loads((worktree / ".lumon.json").read_text())
        # User plugin preserved
        assert "browser" in config["plugins"]
        # Built-in flowstate plugin auto-added
        assert "flowstate" in config["plugins"]

    async def test_default_config_includes_flowstate_plugin(self, worktree: Path) -> None:
        """setup_lumon creates .lumon.json with flowstate plugin when no config specified."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        assert (worktree / ".lumon.json").exists()
        config = json.loads((worktree / ".lumon.json").read_text())
        assert "flowstate" in config["plugins"]

    async def test_default_config_when_no_flow_file_dir(self, worktree: Path) -> None:
        """setup_lumon creates default .lumon.json when flow_file_dir is None."""
        flow = _make_flow(lumon=True, lumon_config="config.json")
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node, flow_file_dir=None)

        assert (worktree / ".lumon.json").exists()
        config = json.loads((worktree / ".lumon.json").read_text())
        assert "flowstate" in config["plugins"]

    async def test_missing_config_file_logs_warning(
        self, worktree: Path, flow_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """setup_lumon warns when config file doesn't exist but still creates default."""
        flow = _make_flow(lumon=True, lumon_config="missing.json")
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node, flow_file_dir=str(flow_dir))

        # Default config still created with flowstate plugin
        assert (worktree / ".lumon.json").exists()
        config = json.loads((worktree / ".lumon.json").read_text())
        assert "flowstate" in config["plugins"]
        assert "not found" in caplog.text

    async def test_skips_nondir_items_in_plugins(self, worktree: Path, tmp_path: Path) -> None:
        """setup_lumon only symlinks directories, not files, from plugin dirs."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        home = tmp_path / "home"
        global_plugins = home / ".flowstate" / "plugins"
        global_plugins.mkdir(parents=True)
        # File, not directory -- should be skipped
        (global_plugins / "README.md").write_text("not a plugin")
        # Directory -- should be symlinked
        (global_plugins / "real_plugin").mkdir()

        with (
            patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec,
            patch("flowstate.engine.lumon.Path.home", return_value=home),
        ):
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        plugins_dir = worktree / "plugins"
        assert (plugins_dir / "real_plugin").is_symlink()
        assert not (plugins_dir / "README.md").exists()

    async def test_no_global_plugins_dir(self, worktree: Path, tmp_path: Path) -> None:
        """setup_lumon handles missing global plugins directory gracefully."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        # Point home to a dir without .flowstate/plugins/
        home = tmp_path / "empty_home"
        home.mkdir()

        with (
            patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec,
            patch("flowstate.engine.lumon.Path.home", return_value=home),
        ):
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        # Should succeed without error
        assert (worktree / "plugins").is_dir()


# ---------------------------------------------------------------------------
# _builtin_plugin_dir tests
# ---------------------------------------------------------------------------


class TestBuiltinPluginDir:
    def test_returns_lumon_plugin_path(self) -> None:
        """_builtin_plugin_dir returns the path to the bundled lumon_plugin."""
        plugin_dir = _builtin_plugin_dir()
        assert plugin_dir.name == "lumon_plugin"
        assert plugin_dir.parent.name == "engine"
        # The directory should actually exist in the source tree
        assert plugin_dir.is_dir()
