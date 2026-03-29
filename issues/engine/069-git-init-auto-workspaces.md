# [ENGINE-069] Initialize auto-created workspaces as git repos

## Domain
engine

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: ENGINE-070

## Spec References
- specs.md Section 2.6 — "Working Directories"
- specs.md Section 9.7 — "Worktree Isolation"

## Summary
When Flowstate auto-creates a workspace at `~/.flowstate/workspaces/<flow-name>/<run-id>/`, initialize it as a git repository with an initial commit. This enables worktree isolation to work for all flows, not just those pointing at existing git repos. Without this, `worktree = true` (the default) silently does nothing for auto-created workspaces.

## Acceptance Criteria
- [ ] Auto-created workspaces have `git init` + initial empty commit run after directory creation
- [ ] `is_git_repo()` returns True for auto-created workspaces
- [ ] Worktree creation succeeds for auto-created workspaces when `worktree = true`
- [ ] User-provided workspaces (flow declares `workspace = "..."`) are NOT modified
- [ ] If `git init` fails (git not installed), workspace creation still succeeds (log warning, skip)
- [ ] Both auto-workspace creation sites updated: `routes.py:_resolve_workspace()` and `queue_manager.py`

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/routes.py` — add git init after `_resolve_workspace()` creates dir
- `src/flowstate/engine/queue_manager.py` — add git init after auto-workspace creation
- `src/flowstate/engine/worktree.py` — add `init_git_repo()` helper
- `tests/engine/test_worktree.py` — test git init on auto workspace

### Key Implementation Details

**New helper in worktree.py:**
```python
async def init_git_repo(path: str) -> bool:
    """Initialize a git repo with an initial commit.

    Returns True if successful, False if git is not available.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "init", cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            return False

        # Initial commit so worktree creation has a HEAD to branch from
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "--allow-empty", "-m", "flowstate: init workspace",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except FileNotFoundError:
        return False
```

**Integration in routes.py and queue_manager.py:**

After `os.makedirs(workspace, exist_ok=True)`, call `await init_git_repo(workspace)`. Log warning if it returns False.

### Edge Cases
- Workspace already exists (re-run of same flow): `git init` is idempotent, no harm
- Git not installed: returns False, workspace works normally without worktree isolation
- Permission errors: caught by try/except, logged, workspace still usable

## Testing Strategy
- Unit test: `init_git_repo()` creates a valid git repo
- Unit test: `is_git_repo()` returns True after `init_git_repo()`
- Integration test: auto-created workspace enables worktree creation

## E2E Verification Plan

### Verification Steps
1. Start server with no `flowstate.toml` (auto workspace mode)
2. Start a flow that doesn't declare `workspace`
3. Check `~/.flowstate/workspaces/<flow>/` — should have `.git/` directory
4. Flow run should show worktree path in run detail

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
