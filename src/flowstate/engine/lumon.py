"""Lumon sandboxing -- deploy, plugin management, and config resolution.

Handles Lumon sandbox setup for task execution:
- Resolving whether Lumon is active for a given flow/node (via ``LumonConfig``).
- Resolving the effective config (node overrides flow entirely, not merged).
- Creating and populating the plugins/ directory with symlinks.
- Writing ``.lumon.json``: either synthesized from ``LumonConfig.plugins``
  or loaded from ``LumonConfig.config_path`` on disk. The built-in
  ``flowstate`` plugin is always added regardless of the source.
- Running `lumon deploy` before subprocess launch.
- Creating the sandbox/ directory.

SHARED-012 migrated the flat ``flow.lumon``/``flow.sandbox`` booleans and
``flow.lumon_config``/``flow.sandbox_policy`` strings to a single
``LumonConfig`` block on ``Flow`` and ``Node``. The legacy flat syntax is
still accepted at the parser layer (see ``_build_lumon_from_flat``) and
collapses to the same ``LumonConfig`` shape, so behavior is preserved.

DSL-016 added the block syntax ``lumon { enabled = true, plugins = [...] }``
which populates ``LumonConfig.plugins`` as a tuple of plugin names. When set,
the engine synthesizes ``.lumon.json`` in-memory rather than reading from disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from flowstate.config import _default_data_dir
from flowstate.engine.context import lumon_plugin_dir

if TYPE_CHECKING:
    from flowstate.dsl.ast import Flow, LumonConfig, Node

logger = logging.getLogger(__name__)


class LumonDeployError(Exception):
    """Raised when `lumon deploy` fails."""


class LumonNotInstalledError(Exception):
    """Raised when the `lumon` CLI binary is not found."""


def _effective_lumon_config(flow: Flow, node: Node) -> LumonConfig | None:
    """Resolve the effective ``LumonConfig`` for ``node``.

    Node-level ``LumonConfig`` fully overrides flow-level when present (it
    does not merge): a node that declares its own ``lumon { ... }`` block
    completely replaces the flow's block. A ``None`` ``lumon`` on the node
    means "inherit from flow".
    """
    if node.lumon is not None:
        return node.lumon
    return flow.lumon


def _use_lumon(flow: Flow, node: Node) -> bool:
    """Check if Lumon sandboxing is active for this node."""
    cfg = _effective_lumon_config(flow, node)
    return cfg is not None and cfg.enabled


def _lumon_config(flow: Flow, node: Node) -> str | None:
    """Resolve the ``.lumon.json`` config path on the effective config.

    Returns ``None`` when no ``config_path`` is set in the effective scope.
    Kept for backward compatibility with callers that only need the path.
    Prefer ``_effective_lumon_config`` when the full block is needed.
    """
    cfg = _effective_lumon_config(flow, node)
    return cfg.config_path if cfg is not None else None


def _builtin_plugin_dir() -> Path:
    """Return the path to the bundled flowstate Lumon plugin directory."""
    return Path(lumon_plugin_dir())


def _symlink_plugins_from(source_dir: Path, target_dir: Path) -> None:
    """Symlink all plugin subdirectories from source_dir into target_dir.

    If a symlink with the same name already exists in target_dir, it is
    replaced (per-flow overrides global).
    """
    if not source_dir.is_dir():
        return
    for plugin in source_dir.iterdir():
        if plugin.is_dir():
            target = target_dir / plugin.name
            if target.is_symlink():
                target.unlink()  # Override existing (e.g., per-flow overrides global)
            if not target.exists():
                target.symlink_to(plugin)


async def setup_lumon(
    worktree_path: str,
    flow: Flow,
    node: Node,
    flow_file_dir: str | None = None,
) -> None:
    """Set up Lumon sandboxing in the worktree.

    Steps:
    1. Create plugins/ directory and symlink plugins (global, per-flow, built-in)
    2. Copy .lumon.json if config specified (resolved relative to flow file dir)
    3. Run ``lumon deploy <worktree> --force``
    4. Create sandbox/ directory

    Raises:
        LumonNotInstalledError: If the ``lumon`` binary is not found.
        LumonDeployError: If ``lumon deploy`` exits with a non-zero code.
    """
    wt = Path(worktree_path)

    # 1. Plugin management
    plugins_dir = wt / "plugins"
    plugins_dir.mkdir(exist_ok=True)

    # Global plugins (<FLOWSTATE_DATA_DIR or ~/.flowstate>/plugins/).
    # ENGINE-083: honor FLOWSTATE_DATA_DIR via flowstate.config._default_data_dir
    # so a relocated data dir (CI, containers, multi-user dev) also relocates
    # the plugin lookup. Re-resolved per call so test monkeypatches take effect.
    global_plugins = _default_data_dir() / "plugins"
    _symlink_plugins_from(global_plugins, plugins_dir)

    # Per-flow plugins (<flow_file_dir>/plugins/)
    if flow_file_dir:
        flow_plugins = Path(flow_file_dir) / "plugins"
        _symlink_plugins_from(flow_plugins, plugins_dir)

    # Built-in flowstate plugin (always included, never overridden by flow)
    builtin = _builtin_plugin_dir()
    if builtin.is_dir():
        target = plugins_dir / "flowstate"
        if not target.exists():
            target.symlink_to(builtin)

    # 2. Build .lumon.json — synthesize from plugins list, load from disk, or
    #    fall back to defaults. The built-in flowstate plugin is always merged
    #    in regardless of which branch is taken.
    cfg = _effective_lumon_config(flow, node)
    lumon_config: dict = {"plugins": {}}

    if cfg is not None and cfg.plugins is not None:
        # Synthesize: explicit plugin list (including empty tuple) takes
        # precedence over any config_path that may also be present in scope.
        # Parser-layer L2 should prevent both being set on the same block,
        # but defense in depth: plugins wins.
        lumon_config = {"plugins": {name: {} for name in cfg.plugins}}
    elif cfg is not None and cfg.config_path and flow_file_dir:
        # Load user config from disk (preserves existing path-based behavior).
        src = Path(flow_file_dir) / cfg.config_path
        if src.exists():
            lumon_config = json.loads(src.read_text())
        else:
            logger.warning(
                "Lumon config '%s' not found at '%s', using defaults", cfg.config_path, src
            )

    # Always register the built-in flowstate plugin
    plugins = lumon_config.setdefault("plugins", {})
    if "flowstate" not in plugins:
        plugins["flowstate"] = {}

    # Write merged config
    (wt / ".lumon.json").write_text(json.dumps(lumon_config, indent=2))

    # 3. Run lumon deploy
    try:
        proc = await asyncio.create_subprocess_exec(
            "lumon",
            "deploy",
            str(wt),
            "--force",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise LumonNotInstalledError(
            "The 'lumon' CLI is not installed or not on PATH. " "Install it with: pip install lumon"
        ) from e

    _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode().strip() if stderr_bytes else "(no stderr)"
        raise LumonDeployError(f"lumon deploy failed (exit code {proc.returncode}): {stderr_text}")

    logger.info("lumon deploy completed for worktree %s", worktree_path)

    # 4. Create sandbox directory
    (wt / "sandbox").mkdir(exist_ok=True)
