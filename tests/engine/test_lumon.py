"""Tests for Lumon sandboxing -- deploy, plugin management, and config resolution.

All tests mock subprocess calls so no real `lumon` binary is needed.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from flowstate.dsl.ast import ContextMode, ErrorPolicy, Flow, LumonConfig, Node, NodeType
from flowstate.engine.lumon import (
    LumonDeployError,
    LumonNotInstalledError,
    _builtin_plugin_dir,
    _effective_lumon_config,
    _lumon_config,
    _use_lumon,
    setup_lumon,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flat_to_config(
    *,
    lumon: bool | None,
    sandbox: bool | None,
    lumon_config: str | None,
    sandbox_policy: str | None,
) -> LumonConfig | None:
    """Mirror the parser-layer mapping from flat syntax onto ``LumonConfig``.

    Kept in the test helpers so existing test cases keep their declarative
    flat-keyword call sites while exercising the post-SHARED-012 AST shape.
    Precedence for ``config_path``: ``lumon_config`` > ``sandbox_policy``
    (preserves prior engine resolution order).
    """
    if lumon is None and sandbox is None and lumon_config is None and sandbox_policy is None:
        return None
    enabled = bool(lumon) or bool(sandbox)
    config_path = lumon_config if lumon_config is not None else sandbox_policy
    return LumonConfig(enabled=enabled, plugins=None, config_path=config_path)


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
        lumon=_flat_to_config(
            lumon=lumon,
            sandbox=sandbox,
            lumon_config=lumon_config,
            sandbox_policy=sandbox_policy,
        ),
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
        lumon=_flat_to_config(
            lumon=lumon,
            sandbox=sandbox,
            lumon_config=lumon_config,
            sandbox_policy=sandbox_policy,
        ),
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
# ENGINE-083: FLOWSTATE_DATA_DIR honors global plugins lookup
# ---------------------------------------------------------------------------


class TestSetupLumonHonorsDataDirEnv:
    """``setup_lumon`` must resolve global plugins under ``FLOWSTATE_DATA_DIR``.

    Before ENGINE-083, ``setup_lumon`` hardcoded ``Path.home() / ".flowstate"
    / "plugins"``, which silently bypassed the data-dir override. After the
    fix, the lookup goes through ``flowstate.config._default_data_dir()``,
    which honors ``FLOWSTATE_DATA_DIR``.
    """

    @pytest.fixture()
    def worktree(self, tmp_path: Path) -> Path:
        wt = tmp_path / "worktree"
        wt.mkdir()
        return wt

    async def test_env_var_redirects_global_plugins_lookup(
        self,
        worktree: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``FLOWSTATE_DATA_DIR`` redirects the global plugins resolver.

        We create the right plugin under ``<custom>/plugins/`` and a decoy
        plugin under a fake ``Path.home() / ".flowstate" / "plugins"``. After
        ``setup_lumon``, the worktree must contain only the right plugin.
        """
        # Wire FLOWSTATE_DATA_DIR to a custom location with a marker plugin.
        custom_data = tmp_path / "fs-custom-data"
        right_plugins = custom_data / "plugins"
        right_plugins.mkdir(parents=True)
        (right_plugins / "myplugin").mkdir()
        (right_plugins / "myplugin" / "marker.txt").write_text("right")

        # Fake home with a decoy plugin under the legacy location. If the
        # lookup still went through Path.home(), this would (incorrectly)
        # be picked up.
        fake_home = tmp_path / "fake_home"
        decoy_plugins = fake_home / ".flowstate" / "plugins"
        decoy_plugins.mkdir(parents=True)
        (decoy_plugins / "wrongplugin").mkdir()
        (decoy_plugins / "wrongplugin" / "marker.txt").write_text("wrong")

        monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(custom_data))

        flow = _make_flow(lumon=True)
        node = _make_node()

        with (
            patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec,
            patch("flowstate.engine.lumon.Path.home", return_value=fake_home),
        ):
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        plugins_dir = worktree / "plugins"
        assert (plugins_dir / "myplugin").is_symlink(), (
            f"Expected 'myplugin' from FLOWSTATE_DATA_DIR; "
            f"actual contents: {sorted(p.name for p in plugins_dir.iterdir())}"
        )
        assert (plugins_dir / "myplugin").resolve() == right_plugins / "myplugin"
        # Decoy must NOT have been picked up.
        assert not (plugins_dir / "wrongplugin").exists(), (
            "Decoy 'wrongplugin' from Path.home() leaked through; "
            "the resolver is not honoring FLOWSTATE_DATA_DIR."
        )

    async def test_unset_env_var_falls_back_to_home(
        self,
        worktree: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With ``FLOWSTATE_DATA_DIR`` unset, the legacy default still works."""
        monkeypatch.delenv("FLOWSTATE_DATA_DIR", raising=False)

        # Build the default location under a fake home so we don't pollute
        # the real ~/.flowstate/.
        fake_home = tmp_path / "fake_home"
        default_plugins = fake_home / ".flowstate" / "plugins"
        default_plugins.mkdir(parents=True)
        (default_plugins / "myplugin").mkdir()
        (default_plugins / "myplugin" / "marker.txt").write_text("default")

        flow = _make_flow(lumon=True)
        node = _make_node()

        with (
            patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec,
            patch("flowstate.engine.lumon.Path.home", return_value=fake_home),
        ):
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        plugins_dir = worktree / "plugins"
        assert (plugins_dir / "myplugin").is_symlink()
        assert (plugins_dir / "myplugin").resolve() == default_plugins / "myplugin"


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


# ---------------------------------------------------------------------------
# ENGINE-087: _effective_lumon_config + plugin-list synthesis
# ---------------------------------------------------------------------------


def _make_flow_with_config(cfg: LumonConfig | None) -> Flow:
    """Build a flow with an explicit ``LumonConfig`` (or None)."""
    return Flow(
        name="test_flow",
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace="/workspace",
        lumon=cfg,
    )


def _make_node_with_config(cfg: LumonConfig | None, name: str = "task1") -> Node:
    """Build a node with an explicit ``LumonConfig`` (or None)."""
    return Node(
        name=name,
        node_type=NodeType.TASK,
        prompt="Do the task",
        lumon=cfg,
    )


class TestEffectiveLumonConfig:
    """``_effective_lumon_config`` returns the node's config when set, else the flow's.

    Node-level config is a full override, not a merge: a node that has
    ``LumonConfig(enabled=True)`` with no plugins/path does NOT inherit
    the flow's plugins or path.
    """

    def test_returns_none_when_neither_set(self) -> None:
        flow = _make_flow_with_config(None)
        node = _make_node_with_config(None)
        assert _effective_lumon_config(flow, node) is None

    def test_returns_flow_when_node_none(self) -> None:
        flow_cfg = LumonConfig(enabled=True, plugins=("filesystem",))
        flow = _make_flow_with_config(flow_cfg)
        node = _make_node_with_config(None)
        assert _effective_lumon_config(flow, node) == flow_cfg

    def test_node_fully_overrides_flow(self) -> None:
        """Node config replaces flow config entirely, no field-merging."""
        flow_cfg = LumonConfig(enabled=True, plugins=("a", "b"), config_path=None)
        node_cfg = LumonConfig(enabled=True, plugins=("x",), config_path=None)
        flow = _make_flow_with_config(flow_cfg)
        node = _make_node_with_config(node_cfg)
        assert _effective_lumon_config(flow, node) == node_cfg

    def test_node_disabled_overrides_flow_enabled(self) -> None:
        """``LumonConfig(enabled=False)`` on a node disables lumon for that node."""
        flow_cfg = LumonConfig(enabled=True, plugins=("a",))
        node_cfg = LumonConfig(enabled=False)
        flow = _make_flow_with_config(flow_cfg)
        node = _make_node_with_config(node_cfg)
        assert _effective_lumon_config(flow, node) == node_cfg
        assert _use_lumon(flow, node) is False

    def test_node_with_plugins_ignores_flow_config_path(self) -> None:
        """Node setting only ``plugins`` does NOT inherit flow's ``config_path``."""
        flow_cfg = LumonConfig(enabled=True, plugins=None, config_path="flow.json")
        node_cfg = LumonConfig(enabled=True, plugins=("x",), config_path=None)
        flow = _make_flow_with_config(flow_cfg)
        node = _make_node_with_config(node_cfg)
        eff = _effective_lumon_config(flow, node)
        assert eff is not None
        assert eff.plugins == ("x",)
        assert eff.config_path is None  # Flow's path is NOT inherited


class TestSetupLumonPluginSynthesis:
    """``setup_lumon`` synthesizes ``.lumon.json`` from ``LumonConfig.plugins``.

    Critical sprint-37b acceptance: when ``lumon { enabled = true,
    plugins = ["filesystem", "git"] }`` is set, the worktree's
    ``.lumon.json`` contains exactly ``{filesystem, git, flowstate}``
    (the two listed + the always-included built-in).
    """

    @pytest.fixture()
    def worktree(self, tmp_path: Path) -> Path:
        wt = tmp_path / "worktree"
        wt.mkdir()
        return wt

    async def test_plugins_list_synthesizes_lumon_json(self, worktree: Path) -> None:
        """``plugins = ["filesystem", "git"]`` -> {filesystem, git, flowstate}."""
        flow = _make_flow_with_config(LumonConfig(enabled=True, plugins=("filesystem", "git")))
        node = _make_node_with_config(None)

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        config = json.loads((worktree / ".lumon.json").read_text())
        # Exactly these three keys, no more, no less.
        assert set(config["plugins"].keys()) == {"filesystem", "git", "flowstate"}
        # Each listed plugin gets an empty per-plugin config object.
        assert config["plugins"]["filesystem"] == {}
        assert config["plugins"]["git"] == {}
        assert config["plugins"]["flowstate"] == {}

    async def test_single_plugin_synthesizes_lumon_json(self, worktree: Path) -> None:
        """``plugins = ["filesystem"]`` -> {filesystem, flowstate}."""
        flow = _make_flow_with_config(LumonConfig(enabled=True, plugins=("filesystem",)))
        node = _make_node_with_config(None)

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        config = json.loads((worktree / ".lumon.json").read_text())
        assert set(config["plugins"].keys()) == {"filesystem", "flowstate"}

    async def test_empty_plugins_tuple_synthesizes_only_flowstate(self, worktree: Path) -> None:
        """``plugins = ()`` (explicit empty) -> only the built-in flowstate plugin."""
        flow = _make_flow_with_config(LumonConfig(enabled=True, plugins=()))
        node = _make_node_with_config(None)

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        config = json.loads((worktree / ".lumon.json").read_text())
        # Empty tuple means "no plugins beyond flowstate".
        assert set(config["plugins"].keys()) == {"flowstate"}

    async def test_plugins_none_falls_back_to_default(self, worktree: Path) -> None:
        """``plugins = None`` (unspecified) with no config_path -> only flowstate."""
        flow = _make_flow_with_config(LumonConfig(enabled=True, plugins=None, config_path=None))
        node = _make_node_with_config(None)

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        config = json.loads((worktree / ".lumon.json").read_text())
        assert set(config["plugins"].keys()) == {"flowstate"}

    async def test_node_plugins_fully_override_flow_config_path(
        self, worktree: Path, tmp_path: Path
    ) -> None:
        """Node with ``plugins`` ignores flow's ``config_path`` entirely.

        Sprint TEST-37b.14: node-level lumon fully overrides flow-level.
        Flow has ``config = "flow.json"``, node has ``plugins = ["x"]``.
        Result: ``.lumon.json`` plugins are exactly {x, flowstate}; the
        flow's ``flow.json`` is NOT loaded.
        """
        flow_dir = tmp_path / "flow_dir"
        flow_dir.mkdir()
        # Write a flow-level config that should NOT be picked up.
        (flow_dir / "flow.json").write_text('{"plugins": {"should_not_appear": {}}}')

        flow = _make_flow_with_config(
            LumonConfig(enabled=True, plugins=None, config_path="flow.json")
        )
        node = _make_node_with_config(LumonConfig(enabled=True, plugins=("x",), config_path=None))

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node, flow_file_dir=str(flow_dir))

        config = json.loads((worktree / ".lumon.json").read_text())
        assert set(config["plugins"].keys()) == {"x", "flowstate"}
        assert "should_not_appear" not in config["plugins"]

    async def test_config_path_branch_preserves_existing_behavior(
        self, worktree: Path, tmp_path: Path
    ) -> None:
        """``config = "policy.json"`` still loads from disk and merges flowstate.

        Sprint TEST-37b.13: backward-compat path-based config still works.
        """
        flow_dir = tmp_path / "flow_dir"
        flow_dir.mkdir()
        (flow_dir / "policy.json").write_text('{"plugins": {"custom": {}}}')

        flow = _make_flow_with_config(
            LumonConfig(enabled=True, plugins=None, config_path="policy.json")
        )
        node = _make_node_with_config(None)

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node, flow_file_dir=str(flow_dir))

        config = json.loads((worktree / ".lumon.json").read_text())
        assert set(config["plugins"].keys()) == {"custom", "flowstate"}

    async def test_node_overrides_flow_plugins_with_different_plugins(self, worktree: Path) -> None:
        """Node ``plugins = ["b"]`` fully overrides flow ``plugins = ["a"]``."""
        flow = _make_flow_with_config(LumonConfig(enabled=True, plugins=("a",)))
        node = _make_node_with_config(LumonConfig(enabled=True, plugins=("b",)))

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        config = json.loads((worktree / ".lumon.json").read_text())
        assert set(config["plugins"].keys()) == {"b", "flowstate"}
        assert "a" not in config["plugins"]

    async def test_plugins_set_ignores_config_path_on_same_block(
        self, worktree: Path, tmp_path: Path
    ) -> None:
        """If both ``plugins`` and ``config_path`` are set, plugins wins.

        L2 should prevent this at parse time, but defense in depth: the
        engine should not crash and should prefer the synthesized list.
        """
        flow_dir = tmp_path / "flow_dir"
        flow_dir.mkdir()
        (flow_dir / "policy.json").write_text('{"plugins": {"from_file": {}}}')

        flow = _make_flow_with_config(
            LumonConfig(enabled=True, plugins=("from_list",), config_path="policy.json")
        )
        node = _make_node_with_config(None)

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node, flow_file_dir=str(flow_dir))

        config = json.loads((worktree / ".lumon.json").read_text())
        # plugins list wins; config_path is ignored.
        assert set(config["plugins"].keys()) == {"from_list", "flowstate"}
        assert "from_file" not in config["plugins"]


class TestSetupLumonBackwardCompat:
    """Flat-syntax flows (``lumon = true``) produce the same ``.lumon.json``
    after ENGINE-087 as before. The parser collapses flat syntax to
    ``LumonConfig(enabled=True, plugins=None, config_path=...)``, which takes
    the default/path branch — never the synthesis branch.

    Sprint TEST-37b.3.
    """

    @pytest.fixture()
    def worktree(self, tmp_path: Path) -> Path:
        wt = tmp_path / "worktree"
        wt.mkdir()
        return wt

    async def test_flat_lumon_true_only_writes_flowstate(self, worktree: Path) -> None:
        """``lumon = true`` (no config) -> ``.lumon.json`` has only flowstate."""
        flow = _make_flow(lumon=True)
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node)

        config = json.loads((worktree / ".lumon.json").read_text())
        assert set(config["plugins"].keys()) == {"flowstate"}

    async def test_flat_lumon_with_config_loads_from_disk(
        self, worktree: Path, tmp_path: Path
    ) -> None:
        """``lumon = true; lumon_config = "x.json"`` still loads from disk."""
        flow_dir = tmp_path / "flow_dir"
        flow_dir.mkdir()
        (flow_dir / "x.json").write_text('{"plugins": {"existing": {}}}')

        flow = _make_flow(lumon=True, lumon_config="x.json")
        node = _make_node()

        with patch("flowstate.engine.lumon.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = _mock_successful_deploy()
            await setup_lumon(str(worktree), flow, node, flow_file_dir=str(flow_dir))

        config = json.loads((worktree / ".lumon.json").read_text())
        assert set(config["plugins"].keys()) == {"existing", "flowstate"}
