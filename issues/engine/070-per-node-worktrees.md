# [ENGINE-070] Per-node worktree isolation with handoff along edges

## Domain
engine

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-069, STATE-011, ENGINE-068
- Blocks: ENGINE-071

## Spec References
- specs.md Section 9.7 — "Worktree Isolation"
- specs.md Section 2.5 — "Routing"
- specs.md Section 6.3 — "Fork-Join Execution"

## Summary
Replace per-run worktree isolation with per-node worktree isolation. Each node gets its own git worktree, and worktree references flow along edges like context:

- **Linear/conditional edge**: next node inherits the predecessor's worktree (reuse — no copy)
- **Fork edge (1 → N)**: each branch gets a new worktree branched from the predecessor's HEAD
- **Join edge (N → 1)**: the join node receives all branch worktrees and merges them before starting

The worktree reference is stored as a `worktree` artifact on each task execution (path + branch name). The flow graph topology directly maps to git branching and merging.

## Acceptance Criteria
- [ ] Entry node creates a worktree from the workspace (or reuses workspace if not a git repo)
- [ ] Linear/conditional edges pass the predecessor's worktree ref to the next node
- [ ] Fork edges create a new worktree branch per fork member from the predecessor's HEAD
- [ ] Join nodes merge all predecessor worktrees before the agent starts
- [ ] Merge conflicts are left as conflict markers for the join agent to resolve
- [ ] The `worktree` artifact stores `{"path": "...", "branch": "...", "original_workspace": "..."}`
- [ ] Context mode `none` starts a fresh worktree from the original workspace HEAD (not predecessor's)
- [ ] Worktrees are cleaned up after the run completes (if `worktree_cleanup = true`)
- [ ] Per-run worktree logic removed (replaced by per-node)
- [ ] `self._worktree_info` (single per-run ref) removed from executor
- [ ] `_apply_worktree_mapping()` reads worktree from task's artifact instead of run-level state
- [ ] `restart_from_task()` handles worktree setup for retried tasks
- [ ] When `sandbox=true`, worktree creation is skipped (sandbox provides its own isolation)

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/worktree.py` — add `create_node_worktree()`, `merge_worktrees()`, `get_worktree_from_artifact()`
- `src/flowstate/engine/executor.py` — replace per-run worktree with per-node; update fork/join/linear paths
- `tests/engine/test_worktree.py` — add per-node worktree tests
- `tests/engine/test_executor.py` — update worktree-related tests

### Key Implementation Details

**Worktree artifact schema:**
```json
{
    "path": "/tmp/flowstate-abc123-xyz/",
    "branch": "flowstate/abc123/analyze-1",
    "original_workspace": "/Users/user/myproject"
}
```

Stored via: `db.save_artifact(task_id, "worktree", json.dumps(ref), "application/json")`

**Entry node setup (executor.py):**
```python
# Create first worktree from workspace
worktree = await create_node_worktree(workspace, run_id, node.name, generation)
db.save_artifact(task_id, "worktree", json.dumps(worktree_to_dict(worktree)), "application/json")
task_cwd = worktree.worktree_path
```

**Linear/conditional edge (executor.py):**
```python
# Read predecessor's worktree artifact
pred_wt = db.get_artifact(predecessor_task_id, "worktree")
if pred_wt:
    # Reuse — next node works in the same worktree
    wt_ref = json.loads(pred_wt.content)
    task_cwd = wt_ref["path"]
    # Save same ref on new task
    db.save_artifact(new_task_id, "worktree", pred_wt.content, "application/json")
```

**Fork edge (executor.py):**
```python
# Read predecessor's worktree
pred_wt = json.loads(db.get_artifact(predecessor_task_id, "worktree").content)

# Create a new branch for each fork member
for member_node in fork_members:
    member_wt = await create_node_worktree(
        pred_wt["original_workspace"], run_id, member_node.name, generation,
        source_branch=pred_wt["branch"],
    )
    db.save_artifact(member_task_id, "worktree", json.dumps(worktree_to_dict(member_wt)), "application/json")
```

**Join node (executor.py):**
```python
# Collect all predecessor worktree refs
member_worktrees = []
for member_task_id in completed_fork_members:
    wt_artifact = db.get_artifact(member_task_id, "worktree")
    if wt_artifact:
        member_worktrees.append(json.loads(wt_artifact.content))

# Create a new worktree for the join, then merge all branches
join_wt = await create_node_worktree(
    original_workspace, run_id, join_node.name, generation,
)
merge_result = await merge_worktrees(join_wt, member_worktrees)

if merge_result.has_conflicts:
    # Leave conflict markers — agent will resolve them
    # Add merge status to the join node's prompt context
    prompt += f"\n\n## Merge Conflicts\nThere are merge conflicts from {len(member_worktrees)} parallel branches. Resolve all conflicts before proceeding with your task.\n"

db.save_artifact(join_task_id, "worktree", json.dumps(worktree_to_dict(join_wt)), "application/json")
```

**New worktree.py functions:**

```python
async def create_node_worktree(
    workspace: str,
    run_id: str,
    node_name: str,
    generation: int,
    source_branch: str | None = None,
) -> WorktreeInfo:
    """Create a worktree for a specific node execution.

    Branch name: flowstate/<run_id[:8]>/<node_name>-<generation>
    If source_branch is provided, branch from it instead of HEAD.
    """

@dataclass
class MergeResult:
    has_conflicts: bool
    conflict_files: list[str]

async def merge_worktrees(
    target: WorktreeInfo,
    sources: list[dict],  # worktree artifact dicts
) -> MergeResult:
    """Merge source branches into the target worktree.

    Uses git merge --no-commit to stage the merge, then checks
    for conflicts. If conflicts exist, leaves them as markers.
    """
```

**Context mode interaction:**
- `handoff`: inherits predecessor's worktree ref (linear) or merges (join)
- `session`: same worktree as predecessor (reuse)
- `none`: creates a fresh worktree from original workspace HEAD

**Cleanup:**
At run completion, collect all `worktree` artifacts for the run, clean up each unique worktree path and branch.

### Edge Cases
- Workspace is not a git repo: skip all worktree logic, all nodes use the raw workspace
- Merge conflict at join: leave markers, add context to join prompt
- Node has no predecessor (entry): create from workspace HEAD
- Cycle re-entry (`handoff` mode): the re-entered node gets a NEW worktree branched from the source node's current HEAD — so it sees the reviewer's/evaluator's changes. The source node's worktree may have uncommitted work; auto-commit before branching.
- Cycle re-entry (`session` mode): reuse the source node's worktree (session resumes in same working directory). No new worktree needed.
- Agent modifies .git state (commits, branches): fine — each worktree is independent
- Same worktree reused by sequential nodes: only clean up after the last user
- **Sandbox + worktree**: When `sandbox=true`, skip per-node worktree creation. The sandbox has its own isolated filesystem — host worktrees are invisible to sandboxed agents. Worktree isolation is redundant when sandbox isolation is active.
- **`restart_from_task()`**: When retrying a failed task, read the failed task's `worktree` artifact. If it exists and the worktree path is still valid, reuse it. If the worktree was cleaned up, create a fresh one from the predecessor's branch (or workspace HEAD for entry nodes).

## Testing Strategy
- Unit test: `create_node_worktree()` creates isolated worktree with correct branch name
- Unit test: `merge_worktrees()` merges cleanly when no conflicts
- Unit test: `merge_worktrees()` reports conflicts and leaves markers
- Integration test: linear flow passes worktree through 3 nodes
- Integration test: fork/join creates branches and merges
- Integration test: merge conflict at join leaves markers for agent

## E2E Verification Plan

### Verification Steps
1. Start server, submit a fork/join flow
2. Verify each fork branch gets its own worktree (different paths)
3. Have fork branches edit different files
4. Verify join node's worktree contains both changes merged
5. Verify worktrees cleaned up after run completes

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
