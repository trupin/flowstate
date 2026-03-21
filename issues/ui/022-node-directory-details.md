# [UI-022] Show working directory, task directory, and git worktree in node details

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: UI-005, ENGINE-025
- Blocks: none

## Summary
When a user clicks on a node in the graph visualization, the expanded node detail panel should show the directory paths the node's agent is working with: the working directory (cwd — where code changes happen), the task directory (where SUMMARY.md and coordination files live), and the git worktree path if the run is using worktree isolation. This gives users visibility into where each agent's files are located, making it easy to inspect outputs or debug issues.

## Acceptance Criteria
- [ ] Clicking a node shows its working directory (`cwd`) path
- [ ] Clicking a node shows its task directory (`task_dir`) path
- [ ] If the run uses a git worktree, the worktree path and branch are shown
- [ ] Paths are displayed in a compact, copyable format (monospace, click-to-copy or selectable)
- [ ] Paths are only shown for nodes that have been executed (have a task execution)
- [ ] Pending/unexecuted nodes show "Not yet executed" or similar

## Technical Design

### Files to Modify
- `src/flowstate/server/models.py` — add `cwd` and `task_dir` fields to `TaskExecutionResponse`
- `src/flowstate/server/routes.py` — populate the new fields from `TaskExecutionRow`
- `ui/src/types.ts` (or API types file) — add `cwd` and `taskDir` to the task type
- `ui/src/components/NodeComponent/` (or equivalent) — render directory info in the expanded detail panel

### Key Implementation Details

**API changes** — `TaskExecutionResponse` needs two new optional fields:
```python
class TaskExecutionResponse(BaseModel):
    ...
    cwd: str | None = None
    task_dir: str | None = None
```

The `TaskExecutionRow` in the state layer already has `cwd` and `task_dir` — they just aren't exposed in the API response model. The route handler that builds `TaskExecutionResponse` from DB rows needs to pass them through.

**UI changes** — In the node detail/expanded view, add a "Directories" section:
```
Directories
  Working dir:  /path/to/repo (or worktree path)
  Task dir:     ~/.flowstate/runs/<id>/tasks/node-1/
  Worktree:     /tmp/flowstate-abc123/ (branch: flowstate/abc123)
```

The worktree info could come from:
- A new field on the run detail response (set by ENGINE-025 when worktrees are implemented)
- Or inferred: if `cwd` differs from the flow's `workspace`, it's likely a worktree

### Edge Cases
- Node not yet executed: show "Awaiting execution" instead of paths
- Node failed before any directory was created: show what's available
- Paths may be long — use monospace + horizontal scroll or truncation with tooltip

## Testing Strategy
- Verify API returns cwd and task_dir for completed tasks
- Visual check: click a completed node, see directory paths
- Click a pending node, see appropriate placeholder
