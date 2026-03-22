# [ENGINE-026] Auto-generate isolated workspace per flow run when workspace is omitted

## Domain
engine

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-025
- Blocks: —

## Summary
Currently, when a flow omits `workspace`, the server defaults to `"."` (the server's CWD), meaning all runs share the same directory. This makes concurrent runs conflict and couples flow execution to the server's working directory. Instead, when `workspace` is not specified in the DSL, Flowstate should auto-generate an isolated temporary workspace for each flow run (e.g., `~/.flowstate/workspaces/<flow-name>/<run-id>/`). This gives every run an independent directory by default, making flows portable and safe for concurrent execution without requiring the flow author to hardcode a path.

When `workspace` IS specified in the DSL, it should continue to work as before (use that path, with optional worktree isolation via ENGINE-025).

## Acceptance Criteria
- [ ] Flows without `workspace` get an auto-generated workspace per run
- [ ] Auto-generated workspace path is predictable: `~/.flowstate/workspaces/<flow-name>/<run-id[:8]>/`
- [ ] Each concurrent run gets a different auto-generated workspace
- [ ] Flows with explicit `workspace` still use the declared path
- [ ] The auto-generated workspace directory is created before task execution starts
- [ ] The auto-generated workspace is stored in the `flow_runs` DB record (in `default_workspace`)
- [ ] The UI shows the actual workspace path (not `"."`)

## Technical Design

### Files to Modify
- `src/flowstate/server/routes.py` — replace `workspace = flow_ast.workspace or "."` with auto-generation logic
- `src/flowstate/engine/executor.py` — ensure `execute()` creates the workspace directory

### Key Implementation Details

**In `routes.py` (lines 216 and 641)**, replace:
```python
workspace = flow_ast.workspace or "."
```

With:
```python
if flow_ast.workspace:
    workspace = flow_ast.workspace
else:
    # Auto-generate isolated workspace for this run
    workspace = os.path.expanduser(
        f"~/.flowstate/workspaces/{flow_ast.name}/{run_id[:8]}"
    )
```

The executor already calls `os.makedirs(workspace, exist_ok=True)` in `execute()` (line ~306), so the directory will be created automatically.

**No DSL/grammar changes needed** — `workspace` remains optional. The behavior change is entirely in the server layer.

**Worktree interaction**: If the auto-generated workspace is not a git repo (which it won't be — it's a fresh empty directory), the worktree logic in ENGINE-025 will correctly skip worktree creation (`is_git_repo()` returns False). This is the desired behavior.

### Edge Cases
- Flow with `workspace = "."` (explicit) — should still use CWD as before
- Flow with `workspace` omitted — auto-generate
- Multiple concurrent runs of the same flow — each gets a unique `<run-id[:8]>` suffix
- Flow reruns — new run_id means new workspace, old workspace persists for inspection
- Cleanup policy for old workspaces — not addressed here (future issue)

## Testing Strategy
- Unit test: mock flow with no workspace, verify auto-generated path is used
- Unit test: mock flow with workspace, verify declared path is used
- Integration test: start two runs of the same flow without workspace, verify different workspaces
- E2E: start a flow without workspace, verify the UI shows the generated workspace path
