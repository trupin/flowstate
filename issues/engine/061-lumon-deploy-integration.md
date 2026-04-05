# [ENGINE-061] Add lumon dependency and integrate deploy into task setup

## Domain
engine

## Status
superseded (by ENGINE-076, ENGINE-077)

## Priority
P0 (critical path)

## Dependencies
- Depends on: DSL-014
- Blocks: ENGINE-062, SERVER-021, UI-065

## Spec References
- specs.md Section 9.9 — "Lumon Security Layer"

## Summary
Add `lumon` as a Python dependency from GitHub and integrate `lumon deploy` into the executor's task setup. When a task has `lumon=true` (resolved via `node.lumon → flow.lumon → false`), the engine deploys Lumon's configuration into the task directory before launching the subprocess. For the SDK harness, explicitly pass the deployed `settings.json` via `ClaudeAgentOptions(settings=...)` so the CLI subprocess loads the PreToolUse hooks. Handle `lumon_config` path resolution and copy to the task directory.

## Acceptance Criteria
- [ ] `lumon` is listed as a dependency in `pyproject.toml` from `git+https://github.com/trupin/lumon.git`
- [ ] When `lumon=true`, `lumon deploy <task-dir>` runs before subprocess launch
- [ ] When `lumon_config` is set, the `.lumon.json` file is copied to `<task-dir>/.lumon.json`
- [ ] `lumon_config` paths are resolved relative to the flow file's directory
- [ ] SDK harness passes `settings=<task-dir>/.claude/settings.json` to `ClaudeAgentOptions`
- [ ] ACP harness works without changes (discovers settings from cwd)
- [ ] Resolution follows the standard pattern: `node.lumon if node.lumon is not None else flow.lumon`
- [ ] If `lumon` package is not installed, error at task start with a clear message
- [ ] All existing tests still pass

## Technical Design

### Files to Create/Modify
- `pyproject.toml` — add lumon dependency
- `src/flowstate/engine/executor.py` — add lumon deploy step in task setup
- `src/flowstate/engine/sdk_runner.py` — pass settings path to ClaudeAgentOptions
- `tests/engine/test_lumon_deploy.py` — unit tests for the deploy integration

### Key Implementation Details

**Dependency (`pyproject.toml`):**
Add to dependencies:
```toml
"lumon @ git+https://github.com/trupin/lumon.git",
```

**Executor (`executor.py`):**

Add a helper function `_use_lumon(flow, node)` following the pattern of `_use_judge` and `_use_subtasks`:
```python
def _use_lumon(flow: Flow, node: Node) -> bool:
    return node.lumon if node.lumon is not None else flow.lumon

def _lumon_config(flow: Flow, node: Node) -> str | None:
    return node.lumon_config if node.lumon_config is not None else flow.lumon_config
```

In `_start_task()`, after the existing sandbox setup and before launching the subprocess:
1. Check `_use_lumon(flow, node)`
2. If true, run `lumon deploy <task_dir>` as a subprocess (or import and call programmatically if available)
3. If `_lumon_config(flow, node)` is set, resolve the path relative to the flow file's parent directory and copy to `<task_dir>/.lumon.json`
4. Store a flag on the task context indicating lumon is active (needed by ENGINE-062 for output path adjustment)

For the deploy step, use `subprocess.run`:
```python
import subprocess
result = subprocess.run(
    ["lumon", "deploy", str(task_dir)],
    capture_output=True, text=True
)
if result.returncode != 0:
    raise RuntimeError(f"lumon deploy failed: {result.stderr}")
```

**SDK Runner (`sdk_runner.py`):**

Add an optional `settings_path` parameter to `run_task()` and `run_task_resume()`:
```python
async def run_task(
    self, prompt: str, workspace: str,
    skip_permissions: bool = False,
    settings_path: str | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    from claude_agent_sdk import ClaudeAgentOptions
    options = ClaudeAgentOptions(cwd=workspace)
    if skip_permissions:
        options.permission_mode = "acceptEdits"
    if settings_path:
        options.settings = settings_path
    ...
```

The executor passes `settings_path=str(task_dir / ".claude" / "settings.json")` when lumon is active and the harness is SDK-based.

For the ACP harness: no changes needed — it discovers `.claude/settings.json` from the working directory automatically.

### Edge Cases
- `lumon` package not installed: catch `FileNotFoundError` from subprocess.run and raise a clear error
- `lumon_config` path doesn't exist: let deploy proceed (lumon itself will validate) but log a warning
- Both `sandbox` and `lumon` enabled: both are applied — sandbox provides OS isolation, lumon provides language-level constraints. Deploy order: sandbox setup first (it creates the container), then lumon deploy inside it
- `lumon deploy` on a directory that already has `.claude/` config: lumon deploy handles this (it won't overwrite existing `.lumon.json`)
- Flow file has no parent directory (e.g., parsed from string): skip lumon_config resolution, log warning

## Testing Strategy
- Unit test: mock subprocess.run, verify `lumon deploy` is called with correct task_dir when lumon=true
- Unit test: verify `lumon deploy` is NOT called when lumon=false
- Unit test: verify settings_path is passed to ClaudeAgentOptions when lumon=true on SDK harness
- Unit test: verify lumon_config is copied to task dir with correct path resolution
- Integration test: verify existing non-lumon flows still work unchanged

## E2E Verification Plan

### Verification Steps
1. Install lumon: `uv pip install git+https://github.com/trupin/lumon.git`
2. Create a `.flow` file with `lumon = true`
3. Start the server: `uv run flowstate serve`
4. Submit a task to the flow
5. Verify the task directory contains lumon deploy artifacts (CLAUDE.md, .claude/settings.json, .claude/hooks/sandbox-guard.py)
6. Verify the subprocess starts and the agent operates within lumon constraints

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in: server restarted, exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
