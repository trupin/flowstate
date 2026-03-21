# [ENGINE-025] Separate workspace (output) from data dir (inter-agent comms) and support git worktrees

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: none
- Blocks: none

## Summary
Currently, flows conflate two directory concepts: the `workspace` (where the agent's `cwd` is set, typically a repository) and the `task_dir` (under `~/.flowstate/runs/<id>/tasks/`, used for SUMMARY.md, DECISION.json, and other inter-agent communication files). The problem is that agents are told to write scratch files to `task_dir` but their actual work (code changes, outputs) happens in `cwd/workspace`. This works when the workspace is a real repo, but breaks when flows try to use an independent temp directory because all the inter-agent files and the actual work get mixed up or misdirected.

The fix involves three changes:

1. **Clear separation**: `workspace` is the deliverable output directory (a repo, a project folder). `data_dir` (already exists as `~/.flowstate/runs/<id>/`) is for Flowstate's internal communication files (SUMMARY.md, INPUT.md, DECISION.json). Prompts should clearly instruct agents: "work in `cwd`, write coordination files to `task_dir`."

2. **Git worktree isolation**: When `workspace` points to a git repository, each flow run should create its own git worktree so concurrent runs don't conflict. The worktree is created at run start (e.g., `git worktree add /tmp/flowstate-worktree-<run-id> -b flowstate/<run-id>`) and the resolved worktree path is used as the effective `cwd` for all tasks in the run.

3. **Cleanup**: On flow completion/cancellation, the worktree can be cleaned up (or left for inspection, configurable).

## Acceptance Criteria
- [ ] Agents work in `cwd` (workspace) and write coordination files to `task_dir` — these are always separate locations
- [ ] When `workspace` is a git repo, each flow run gets its own worktree branch
- [ ] The worktree path is used as the effective `cwd` for all tasks in the run
- [ ] Concurrent runs on the same repo do not conflict (isolated branches)
- [ ] Prompts clearly distinguish "your working directory" from "your task coordination directory"
- [ ] Worktrees are cleaned up on flow completion (configurable: auto-clean vs. keep)
- [ ] Non-git workspaces work as before (no worktree, direct cwd)

## Technical Design

### Files to Modify
- `src/flowstate/engine/executor.py` — at run start, detect git repo, create worktree, use worktree path as effective cwd
- `src/flowstate/engine/context.py` — prompts already separate `cwd` and `task_dir`; verify instructions are clear
- `src/flowstate/dsl/ast.py` — possibly add `worktree: bool = True` field to Flow (opt-out)
- `src/flowstate/dsl/grammar.lark` — possibly add `worktree = true/false` flow attribute
- `src/flowstate/config.py` — add `worktree_cleanup: bool = True` config option

### Key Implementation Details

**Git worktree creation** (in executor.py `execute()` before main loop):
```python
import subprocess
workspace_path = Path(workspace)
if (workspace_path / ".git").exists() or (workspace_path / ".git").is_file():
    # Create isolated worktree for this run
    branch = f"flowstate/{flow_run_id[:8]}"
    worktree_path = Path(tempfile.mkdtemp(prefix=f"flowstate-{flow_run_id[:8]}-"))
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "-b", branch],
        cwd=workspace, check=True
    )
    effective_workspace = str(worktree_path)
else:
    effective_workspace = workspace
```

**Cleanup** (in `_complete_flow` and `cancel`):
```python
if worktree_path and worktree_cleanup:
    subprocess.run(["git", "worktree", "remove", str(worktree_path)], cwd=workspace)
```

**Prompt clarity** — verify that `build_prompt_handoff/none/join` clearly say:
- "Your working directory is: {cwd}" (where to make code changes)
- "Write coordination files (SUMMARY.md) to: {task_dir}" (Flowstate internal)

### Edge Cases
- Workspace is not a git repo → skip worktree, use workspace directly
- Workspace is a git worktree already → detect and skip (don't nest worktrees)
- Worktree creation fails (dirty state, branch exists) → fall back to direct workspace, log warning
- Flow cancellation mid-execution → still clean up worktree
- Multiple concurrent runs → each gets unique branch name via run_id

## Testing Strategy
- Unit test: mock git commands, verify worktree creation/cleanup logic
- Integration test: create a temp git repo, start a flow, verify worktree is created and agents work in it
- Test non-git workspace: verify no worktree created, agents work directly
