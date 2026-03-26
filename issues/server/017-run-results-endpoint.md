# [SERVER-017] Add GET /api/runs/{run_id}/results endpoint for run output summary

## Domain
server

## Status
done

## Priority
P1

## Dependencies
- Depends on: —
- Blocks: UI-046

## Summary
Add an API endpoint that computes and returns the output of a completed run. For git-based workspaces, returns the `git diff` between the original branch and the worktree. For non-git workspaces, returns a list of files created/modified. Always includes task SUMMARY.md content from each node.

## Acceptance Criteria
- [ ] `GET /api/runs/{run_id}/results` returns computed results for completed runs
- [ ] When workspace is a git repo with a worktree: includes `git_diff` (unified diff string)
- [ ] When workspace is NOT a git repo: includes `file_changes` list (created/modified files)
- [ ] Always includes `task_summaries` — map of node_name → SUMMARY.md content
- [ ] Returns 400 if run is not in a terminal status
- [ ] Returns 404 if run doesn't exist

## Technical Design

### New file: `src/flowstate/engine/results.py`

```python
async def compute_run_results(run: FlowRunRow, db: FlowstateDB) -> dict:
    workspace = run.worktree_path or run.default_workspace

    # Git diff (if applicable)
    git_diff = None
    if workspace and is_git_repo(workspace):
        git_diff = await asyncio.to_thread(
            subprocess.check_output,
            ["git", "diff", "HEAD"], cwd=workspace, text=True
        )

    # File changes (for non-git workspaces)
    file_changes = None
    if workspace and not is_git_repo(workspace):
        # List files modified after run.started_at
        file_changes = list_modified_files(workspace, since=run.started_at)

    # Task summaries
    task_summaries = {}
    for te in db.list_task_executions(run.id):
        if te.task_dir:
            summary = read_summary(te.task_dir)
            if summary:
                task_summaries[te.node_name] = summary

    return {
        "workspace": workspace,
        "git_available": git_diff is not None,
        "git_diff": git_diff,
        "file_changes": file_changes,
        "task_summaries": task_summaries,
    }
```

### Endpoint in `src/flowstate/server/routes.py`
```
GET /api/runs/{run_id}/results
Response: {
    workspace: string | null,
    git_available: boolean,
    git_diff: string | null,
    file_changes: [{path, status}] | null,
    task_summaries: {[node_name]: string}
}
```

### Files to Modify
- `src/flowstate/engine/results.py` — New file with `compute_run_results()`
- `src/flowstate/server/routes.py` — New endpoint
- `src/flowstate/server/models.py` — `RunResultsResponse` model

### Edge Cases
- Worktree may have been cleaned up after completion → return null git_diff with a message
- Non-git workspace with no file changes → return empty list
- No SUMMARY.md files → return empty task_summaries dict

## Testing Strategy
- Unit test: compute results with a mock git workspace
- Unit test: compute results with non-git workspace
- Route test: GET results for completed run → 200
- Route test: GET results for running run → 400

## Completion Checklist
- [ ] Implementation complete
- [ ] Tests passing
- [ ] `/lint` passes
