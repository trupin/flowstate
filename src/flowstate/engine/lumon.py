"""Lumon sandboxing -- deploy, plugin management, and config resolution.

Handles Lumon sandbox setup for task execution:
- Resolving whether Lumon is active for a given flow/node
- Resolving the .lumon.json config path (node overrides flow, sandbox_policy aliases lumon_config)
- Creating and populating the plugins/ directory with symlinks
- Copying .lumon.json config when specified
- Running `lumon deploy` before subprocess launch
- Creating the sandbox/ directory
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from flowstate.engine.context import lumon_plugin_dir

if TYPE_CHECKING:
    from flowstate.dsl.ast import Flow, Node

logger = logging.getLogger(__name__)


class LumonDeployError(Exception):
    """Raised when `lumon deploy` fails."""


class LumonNotInstalledError(Exception):
    """Raised when the `lumon` CLI binary is not found."""


def _use_lumon(flow: Flow, node: Node) -> bool:
    """Check if Lumon sandboxing is active for this node.

    Returns True when the node or flow has lumon=True or sandbox=True.
    Node-level settings override flow-level (None means inherit).
    """
    lumon = node.lumon if node.lumon is not None else flow.lumon
    sandbox = node.sandbox if node.sandbox is not None else flow.sandbox
    return bool(lumon or sandbox)


def _lumon_config(flow: Flow, node: Node) -> str | None:
    """Resolve the .lumon.json config path.

    Priority: node.lumon_config > node.sandbox_policy > flow.lumon_config > flow.sandbox_policy.
    Returns None if no config is specified at any level.
    """
    if node.lumon_config is not None:
        return node.lumon_config
    if node.sandbox_policy is not None:
        return node.sandbox_policy
    if flow.lumon_config is not None:
        return flow.lumon_config
    if flow.sandbox_policy is not None:
        return flow.sandbox_policy
    return None


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

    # Global plugins (~/.flowstate/plugins/)
    global_plugins = Path.home() / ".flowstate" / "plugins"
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

    # 2. Build .lumon.json — merge user config with built-in flowstate plugin
    config_path = _lumon_config(flow, node)
    lumon_config: dict = {"plugins": {}}

    # Load user config if specified
    if config_path and flow_file_dir:
        src = Path(flow_file_dir) / config_path
        if src.exists():
            lumon_config = json.loads(src.read_text())
        else:
            logger.warning("Lumon config '%s' not found at '%s', using defaults", config_path, src)

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
