# [ENGINE-077] Lumon deploy integration with plugin management

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-076
- Blocks: —

## Spec References
- specs.md Section 9.9 — "Lumon Sandboxing"

## Summary
Integrate Lumon deployment into the executor's task lifecycle. When `sandbox = true` or `lumon = true`, the engine: resolves and symlinks plugins (global + per-flow + built-in flowstate), copies the lumon_config, runs `lumon deploy`, and creates the sandbox directory — all before launching the agent subprocess. This replaces the stale ENGINE-061/062 issues.

## Acceptance Criteria
- [ ] `lumon` listed as dependency in `pyproject.toml` from `git+https://github.com/trupin/lumon.git`
- [ ] New module `src/flowstate/engine/lumon.py` with: `setup_lumon()`, `_use_lumon()`, `_lumon_config()`
- [ ] `_use_lumon()` returns True when `node.lumon or flow.lumon or node.sandbox or flow.sandbox`
- [ ] `_lumon_config()` resolves: node.lumon_config ?? node.sandbox_policy ?? flow.lumon_config ?? flow.sandbox_policy
- [ ] Plugin management: `<worktree>/plugins/` created with symlinks to:
  - Global plugins from `~/.flowstate/plugins/`
  - Per-flow plugins from `<flow-file-dir>/plugins/`
  - Built-in flowstate plugin from `src/flowstate/engine/lumon_plugin/`
  - Per-flow overrides global (same name → per-flow wins)
- [ ] `.lumon.json` copied to `<worktree>/.lumon.json` when lumon_config is set (resolved relative to flow file dir)
- [ ] `lumon deploy <worktree> --force` runs before subprocess launch
- [ ] `<worktree>/sandbox/` directory created
- [ ] If `lumon` package is not installed, clear error at task start
- [ ] If `lumon deploy` fails, task fails with clear error
- [ ] Executor `_execute_single_task()` calls `setup_lumon()` before harness launch when `_use_lumon()` is True
- [ ] SDK harness: pass `settings=<worktree>/.claude/settings.json` to `ClaudeAgentOptions` when Lumon active
- [ ] ACP harness: no changes (discovers settings from cwd)
- [ ] All existing engine tests pass
- [ ] New tests for Lumon setup, plugin resolution, config copy

## Technical Design

### Files to Create

**`src/flowstate/engine/lumon.py`:**
```python
"""Lumon sandboxing — deploy, plugin management, and config resolution."""

import asyncio
import logging
import os
import shutil
from pathlib import Path

from flowstate.dsl.ast import Flow, Node

logger = logging.getLogger(__name__)


def _use_lumon(flow: Flow, node: Node) -> bool:
    """Check if Lumon sandboxing is active for this node."""
    lumon = node.lumon if node.lumon is not None else flow.lumon
    sandbox = node.sandbox if node.sandbox is not None else flow.sandbox
    return lumon or sandbox


def _lumon_config(flow: Flow, node: Node) -> str | None:
    """Resolve the .lumon.json config path (node overrides flow, sandbox_policy aliases lumon_config)."""
    config = node.lumon_config or node.sandbox_policy
    if config is not None:
        return config
    return flow.lumon_config or flow.sandbox_policy


async def setup_lumon(
    worktree_path: str,
    flow: Flow,
    node: Node,
    flow_file_dir: str | None = None,
) -> None:
    """Set up Lumon sandboxing in the worktree.

    1. Resolve and symlink plugins (global + per-flow + built-in)
    2. Copy .lumon.json if config specified
    3. Run lumon deploy
    4. Create sandbox/ directory
    """
    wt = Path(worktree_path)

    # 1. Plugin management
    plugins_dir = wt / "plugins"
    plugins_dir.mkdir(exist_ok=True)

    # Global plugins
    global_plugins = Path.home() / ".flowstate" / "plugins"
    if global_plugins.is_dir():
        for plugin in global_plugins.iterdir():
            if plugin.is_dir():
                target = plugins_dir / plugin.name
                if not target.exists():
                    target.symlink_to(plugin)

    # Per-flow plugins
    if flow_file_dir:
        flow_plugins = Path(flow_file_dir) / "plugins"
        if flow_plugins.is_dir():
            for plugin in flow_plugins.iterdir():
                if plugin.is_dir():
                    target = plugins_dir / plugin.name
                    if target.is_symlink():
                        target.unlink()  # Override global
                    if not target.exists():
                        target.symlink_to(plugin)

    # Built-in flowstate plugin (always included)
    builtin = Path(__file__).parent / "lumon_plugin"
    if builtin.is_dir():
        target = plugins_dir / "flowstate"
        if not target.exists():
            target.symlink_to(builtin)

    # 2. Copy .lumon.json
    config_path = _lumon_config(flow, node)
    if config_path and flow_file_dir:
        src = Path(flow_file_dir) / config_path
        if src.exists():
            shutil.copy2(str(src), str(wt / ".lumon.json"))

    # 3. Deploy
    proc = await asyncio.create_subprocess_exec(
        "lumon", "deploy", str(wt), "--force",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"lumon deploy failed: {stderr.decode().strip()}")

    # 4. Create sandbox dir
    (wt / "sandbox").mkdir(exist_ok=True)
```

### Files to Modify

**`pyproject.toml`:**
- Add `"lumon @ git+https://github.com/trupin/lumon.git"` to dependencies

**`src/flowstate/engine/executor.py`:**
- Import `from flowstate.engine.lumon import _use_lumon, setup_lumon`
- In `_execute_single_task()`, after worktree creation and before harness launch:
  ```python
  if _use_lumon(flow, node):
      await setup_lumon(task_cwd, flow, node, flow_file_dir=self._flow_file_dir)
  ```
- Need to track `flow_file_dir` — the directory containing the .flow file (for resolving relative paths)
- For SDK harness when Lumon active: pass `settings` option

**`src/flowstate/engine/sdk_runner.py`:**
- Add optional `settings` parameter to `run_task()` and `run_task_resume()`
- When provided: `ClaudeAgentOptions(settings=settings)`

### Edge Cases
- `lumon` not installed: `FileNotFoundError` from `create_subprocess_exec` → catch and raise clear error
- `lumon deploy` fails (e.g., directory permissions): propagate error, task fails
- No global plugins dir: skip (mkdir on first use)
- No per-flow plugins dir: skip
- Plugin name collision: per-flow wins (symlink replaced)
- Flow file dir unknown (queue manager path): resolve from flow registry

## Testing Strategy
- Unit test `_use_lumon()`: various combinations of flow/node sandbox/lumon flags
- Unit test `_lumon_config()`: resolution priority
- Unit test `setup_lumon()`: mock subprocess, verify plugin symlinks, config copy, deploy call
- Integration test: verify `lumon deploy` creates expected files in a temp worktree

## E2E Verification Plan

### Verification Steps
1. Create a flow with `sandbox = true`
2. Place plugins in `~/.flowstate/plugins/`
3. Start server, submit flow
4. Verify `<worktree>/.claude/settings.json` exists
5. Verify `<worktree>/plugins/flowstate/` symlink exists
6. Verify `<worktree>/sandbox/` directory created
7. Verify agent is constrained (only lumon commands work)

## E2E Verification Log
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
