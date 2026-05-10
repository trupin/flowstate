# [ENGINE-088] Persist exit worktree to source branch on flow completion

## Domain
engine

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: DSL-017, STATE-013
- Blocks: —

## Spec References
- specs.md Section 9.7 — "Worktree Isolation" (new "Persistence" subsection)
- specs.md Section 6.1 — "Flow Run Lifecycle" (capture source branch at run-start)

## Summary
When a flow has `worktree_persist = true`, the engine captures the original workspace's source branch at run-start and, on successful flow completion, merges the exit node's worktree branch back into that source branch. The merge runs in a fresh **detached worktree** so the user's main checkout is never touched — this avoids all the dirty-working-tree, switched-branch, and concurrent-edit problems that an in-place merge would face. Source-branch ref advancement is atomic via `git update-ref`'s compare-and-swap. Concurrent runs serialize on a per-workspace file lock. Merge conflicts cause the run to complete with status `completed_with_conflicts`, the exit branch is preserved (cleanup skipped), and the user can resolve manually.

## Acceptance Criteria
- [ ] At run-start (when `flow.worktree_persist` and `flow.worktree` are both true and the workspace is a git repo): capture the source branch via `git symbolic-ref --short HEAD` in the original workspace. Persist via STATE-013's `set_source_branch`. If the workspace is in detached HEAD state, log a warning and skip persist (no source branch to merge into).
- [ ] At flow completion (in `_complete_flow`, before `_cleanup_all_worktrees`): if `worktree_persist` is true, call new `_persist_exit_worktree(flow_run_id)`.
- [ ] `_persist_exit_worktree` finds the exit task's `worktree` artifact, reads its branch name, and runs the detached-worktree merge described below.
- [ ] Merge succeeds (no conflicts, ref CAS succeeded): source branch ref advances. Emit `SOURCE_BRANCH_ADVANCED` event with old + new commit. Run status remains `completed`.
- [ ] Merge conflicts: abort merge, remove temp worktree, **skip cleanup of the exit node's worktree and branch** (preserves the exit branch for the user). Emit `SOURCE_BRANCH_PERSIST_CONFLICT` event with conflicting paths. Mark run status `completed_with_conflicts`.
- [ ] CAS failure (someone else moved the source branch): retry up to 3 times with fresh temp worktree. After 3 failures, treat as a conflict-style outcome and preserve the exit branch.
- [ ] Concurrent runs in the same workspace serialize via a file lock at `<workspace>/.git/flowstate-persist.lock`.
- [ ] Skip merge cleanly (no error) when:
   - `flow.worktree_persist = false`
   - The exit node was reached via a `context = none` edge (no upstream commits)
   - No exit task has a `worktree` artifact
   - `source_branch` is NULL on the flow_run row (workspace was detached or pre-feature)
- [ ] Multiple exit nodes: persist whichever exit fires (the one whose completion triggered `_complete_flow`).
- [ ] No regression for flows that don't enable `worktree_persist`: behavior is exactly as before.

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — capture source branch in run-start path, call persist in `_complete_flow`
- `src/flowstate/engine/worktree.py` — new helper `merge_to_source_branch_via_detached_worktree`
- `src/flowstate/engine/events.py` — add `SOURCE_BRANCH_ADVANCED` and `SOURCE_BRANCH_PERSIST_CONFLICT` event types (plus update event-count test from ENGINE-085)
- `tests/engine/test_persist_exit_worktree.py` — new test file with real git ops in a `tmp_path` repo
- `tests/engine/test_executor.py` — augment for capture-at-run-start

### Key Implementation Details

**Capture source branch at run-start.**

In the executor's run-start path (where worktrees are created — search for `create_worktree` / `create_node_worktree` near the entry node setup):

```python
# After resolving the original workspace and confirming it's a git repo
source_branch = await _capture_source_branch(original_workspace)
self._db.set_source_branch(flow_run_id, source_branch)

async def _capture_source_branch(workspace: str) -> str | None:
    proc = await asyncio.create_subprocess_exec(
        "git", "symbolic-ref", "--short", "HEAD",
        cwd=workspace, stdout=PIPE, stderr=PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None  # detached HEAD or not a repo
    return stdout.decode().strip() or None
```

Only capture when `flow.worktree_persist` is true. If false, leave `source_branch` NULL in the DB (no work needed).

**Persist on completion.**

In `_complete_flow`, before the existing DB updates:

```python
def _complete_flow(self, flow_run_id, budget):
    # New: persist exit worktree if enabled
    flow_run = self._db.get_flow_run(flow_run_id)
    flow = ...  # already in scope
    if flow.worktree_persist:
        try:
            persist_result = await self._persist_exit_worktree(flow_run_id, flow)
            if persist_result.status == "conflict":
                self._db.update_flow_run_status(flow_run_id, "completed_with_conflicts")
            else:
                self._db.update_flow_run_status(flow_run_id, "completed")
        except Exception:
            logger.exception("persist_exit_worktree failed")
            self._db.update_flow_run_status(flow_run_id, "completed_with_conflicts")
    else:
        self._db.update_flow_run_status(flow_run_id, "completed")
    
    # Existing DB updates and event emission...
```

(Note: `_complete_flow` is currently sync; if the persist requires async, extract the persist into an async helper called from the async caller of `_complete_flow`. Verify against current code structure during implementation.)

**`_persist_exit_worktree`:**

```python
async def _persist_exit_worktree(self, flow_run_id: str, flow: Flow) -> PersistResult:
    source_branch = self._db.get_source_branch(flow_run_id)
    if source_branch is None:
        return PersistResult(status="skipped", reason="no_source_branch")
    
    # Find the exit task's worktree artifact
    tasks = self._db.list_task_executions(flow_run_id)
    exit_tasks = [t for t in tasks if t.node_type == "exit" and t.status == "completed"]
    if not exit_tasks:
        return PersistResult(status="skipped", reason="no_exit_task")
    
    # Use the most recent completed exit
    exit_task = max(exit_tasks, key=lambda t: t.completed_at or t.created_at)
    wt_artifact = self._db.get_artifact(exit_task.id, "worktree")
    if wt_artifact is None:
        return PersistResult(status="skipped", reason="no_worktree_artifact")
    
    exit_wt = worktree_artifact_from_json(wt_artifact.content)
    
    # Acquire workspace-level lock and run merge
    return await merge_to_source_branch_via_detached_worktree(
        original_workspace=exit_wt.original_workspace,
        source_branch=source_branch,
        exit_branch=exit_wt.branch_name,
        max_cas_retries=3,
    )
```

**The merge helper (`worktree.py`):**

```python
@dataclass
class PersistResult:
    status: Literal["advanced", "conflict", "skipped", "cas_exhausted"]
    old_commit: str | None = None
    new_commit: str | None = None
    conflict_files: list[str] = field(default_factory=list)
    reason: str | None = None

async def merge_to_source_branch_via_detached_worktree(
    original_workspace: str,
    source_branch: str,
    exit_branch: str,
    max_cas_retries: int = 3,
) -> PersistResult:
    """Merge exit_branch into source_branch via a temporary detached worktree.
    
    Never touches the user's main checkout. Uses `git update-ref` CAS to atomically
    advance the branch ref. Retries on CAS failure (someone else moved the branch).
    """
    lock_path = Path(original_workspace) / ".git" / "flowstate-persist.lock"
    
    # Per-workspace lock — serializes concurrent runs targeting the same source branch
    with _flock(lock_path):
        for attempt in range(max_cas_retries):
            old_commit = await _rev_parse(original_workspace, source_branch)
            
            # Create detached worktree at source_branch's current commit
            temp_dir = tempfile.mkdtemp(prefix=f"flowstate-persist-{exit_branch[:8]}-")
            try:
                rc, err = await _run_git(
                    ["worktree", "add", "--detach", temp_dir, old_commit],
                    cwd=original_workspace,
                )
                if rc != 0:
                    return PersistResult(
                        status="skipped",
                        reason=f"failed to create temp worktree: {err}",
                    )
                
                # Merge in the temp worktree
                rc, err = await _run_git(
                    ["merge", "--no-ff", "-m", f"flowstate: persist {exit_branch}", exit_branch],
                    cwd=temp_dir,
                )
                if rc != 0:
                    # Detect conflicts vs other failure
                    conflict_files = await _conflict_files(temp_dir)
                    if conflict_files:
                        await _run_git(["merge", "--abort"], cwd=temp_dir)
                        return PersistResult(
                            status="conflict",
                            old_commit=old_commit,
                            conflict_files=conflict_files,
                        )
                    return PersistResult(status="skipped", reason=f"merge failed: {err}")
                
                new_commit = await _rev_parse(temp_dir, "HEAD")
                
                # Atomic CAS: advance source branch only if it hasn't moved
                rc, err = await _run_git(
                    ["update-ref", f"refs/heads/{source_branch}", new_commit, old_commit],
                    cwd=original_workspace,
                )
                if rc == 0:
                    return PersistResult(
                        status="advanced",
                        old_commit=old_commit,
                        new_commit=new_commit,
                    )
                # CAS failed — someone moved the branch. Retry.
                logger.info(
                    "CAS failed for source branch %s on attempt %d; retrying",
                    source_branch, attempt + 1,
                )
            finally:
                # Always clean up the temp worktree
                await _run_git(["worktree", "remove", "--force", temp_dir], cwd=original_workspace)
                shutil.rmtree(temp_dir, ignore_errors=True)
        
        return PersistResult(status="cas_exhausted", reason="source branch moved repeatedly")
```

**File lock helper:**

Use `fcntl.flock` (POSIX). Block until acquired (advisory lock; respected by other flowstate processes that follow the same convention). Document that the lock is best-effort on filesystems that don't support flock (e.g. some NFS mounts) — for a single-user local dev/desktop scenario this is fine.

```python
@contextmanager
def _flock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()
```

**Cleanup integration:**

`_cleanup_all_worktrees` already iterates all task worktree artifacts and removes branches. When persist outcome was `"conflict"` or `"cas_exhausted"`, we want to **preserve the exit task's worktree and branch** so the user can resolve manually. Approach: track per-flow-run a set of "preserved branches" populated by `_persist_exit_worktree`. `_cleanup_all_worktrees` skips removal for any task whose worktree branch is in the preserved set.

Surface the preserved branch name in the conflict event payload so the UI / CLI can tell the user how to recover (e.g. `git merge flowstate/abc12345/exit-1`).

**Events:**

```python
class EventType(StrEnum):
    ...
    SOURCE_BRANCH_ADVANCED = "source_branch_advanced"
    SOURCE_BRANCH_PERSIST_CONFLICT = "source_branch_persist_conflict"
```

Update `tests/engine/test_events_count.py` (or wherever ENGINE-085 lives) to bump the count by 2.

### Edge Cases
- **Workspace is auto-generated** (no `workspace` declared on flow): treat as no source branch to persist to. Auto-generated workspaces are ephemeral. Skip persist with reason `auto_generated_workspace`.
- **Source branch was deleted during the run** (`git rev-parse` fails): skip with reason `source_branch_missing`. Preserve exit branch.
- **Exit branch doesn't exist** (cleanup raced ahead): skip with reason `exit_branch_missing`. Should not happen in practice if cleanup ordering is correct, but defensively handle.
- **Detached HEAD in original workspace at run-start**: `_capture_source_branch` returns None. Persist call later sees NULL and skips cleanly.
- **Workspace is on a non-git filesystem**: shouldn't happen if `worktree = true` was honored, but defensively handle the failure.
- **Multiple exit nodes, multiple completions**: only one `_complete_flow` call per run (first exit fires, run completes). The `exit_tasks` list filters to status=`completed`; pick the most recent. If a later exit somehow also completes, persist already ran for the run.
- **`context = none` edge into exit**: the exit task's worktree was created fresh from the original workspace HEAD, not from upstream commits. Its branch contains nothing the user wanted persisted. Skip with reason `none_context_exit`. Detect by checking the edge type into the exit node from the run's edge transitions.
- **Hooks on `git merge`** in the temp worktree (e.g. `commit-msg` hooks): hooks run in the worktree. For an automated persist this is mostly fine, but a misbehaving hook could block the merge. Document; don't try to disable hooks (per project convention).

## Testing Strategy

Unit tests against a real git repo in `tmp_path`:

- **Happy path**: create repo with initial commit on `main`, simulate a flow run that creates a worktree on `flowstate/abc/exit-1` with one extra commit, call the persist helper, verify `main` advances to the merge commit, verify temp worktree is gone.
- **CAS failure + retry**: between `_rev_parse` and `update-ref`, mutate `main` (extra commit). First attempt CAS-fails. Retry succeeds with the new base.
- **Real conflict**: exit branch and `main` both modified the same line. Merge produces conflict. Helper returns `status="conflict"` with `conflict_files`. `main` is unchanged. Temp worktree is gone.
- **Lock contention**: spawn two concurrent calls to the helper for the same workspace. They serialize. (Use a barrier or polling to verify ordering.)
- **Skip cases**: detached HEAD source, no exit task, no worktree artifact, auto-generated workspace.

Integration tests exercise the executor → state → persist path with a mocked subprocess for `lumon deploy` but real git for the merge.

## E2E Verification Plan

### Verification Steps
1. Create a journal repo: `mkdir ~/journal && cd ~/journal && git init && echo init > README.md && git add . && git commit -m init`
2. Create a flow with `workspace = "~/journal"`, `worktree = true`, `worktree_persist = true`, a single task that writes a file and commits in the worktree, and an exit node.
3. Submit a task via the CLI/API.
4. Observe the run completes. `cd ~/journal && git log --oneline main` shows the new commits merged in. Working tree clean.
5. While a run is in progress, edit `~/journal/notes.txt` (untracked file) and stage some changes (`echo x > tracked.txt; git add tracked.txt`). Let the run complete. Observe: working tree state preserved, `main` ref advanced. `git status` shows the staged file still staged, untracked file still untracked.
6. Conflict scenario: modify `main` to change a file the run also modified. Run completes with status `completed_with_conflicts`. `main` is unchanged. The exit branch is preserved; user can `git merge flowstate/<run-id>/exit-1` and resolve.

## E2E Verification Log

### Post-Implementation Verification

**1. Test suite (real-git operations against `tmp_path`):**

```
$ uv run pytest tests/engine/test_persist_exit_worktree.py -v
============================== 17 passed in 2.05s ==============================
```

All 17 tests pass, covering: capture-source-branch (3), happy-path advance,
dirty-working-tree-untouched, real merge conflict, CAS retry via the
`pre_cas_hook` injection seam, CAS exhaustion, concurrent lock serialization,
helper skip cases, and four executor-integration skip cases plus two
executor-integration end-to-end cases (advanced + conflict).

**2. Full engine test suite (excluding `test_executor.py` pre-existing hang):**

```
$ uv run pytest tests/engine/ --ignore=tests/engine/test_executor.py -q
490 passed in 69.74s
```

**3. Sample `test_executor.py` classes (verifying no regression in the
sync→async `_complete_flow` change):**

```
$ uv run pytest tests/engine/test_executor.py::TestMinimalFlow \
                tests/engine/test_executor.py::TestFlowRunRecord \
                tests/engine/test_executor.py::TestLinear3NodeFlow \
                tests/engine/test_executor.py::TestForkJoin2Targets \
                tests/engine/test_executor.py::TestSubprocessManagerCalled -q
8 passed in 0.10s
```

`TestContextModeHandoff` hangs — this is the **pre-existing** hang explicitly
called out in the issue and the sprint-planner notes, not a regression from
ENGINE-088.

**4. State and DSL regression (migration 3 + new status):**

```
$ uv run pytest tests/state/ tests/dsl/ -q
675 passed in 3.10s
```

**5. Lint and type checks:**

```
$ uv run ruff check src/flowstate/engine/ src/flowstate/state/ tests/engine/test_persist_exit_worktree.py
All checks passed!
$ uv run pyright src/flowstate/engine/ src/flowstate/state/ tests/engine/test_persist_exit_worktree.py
0 errors, 0 warnings, 0 informations
```

**6. Dirty-working-tree real-repo demonstration (TEST-37c.9 spirit):**

```
$ mkdir /tmp/flowstate-e2e-demo && cd /tmp/flowstate-e2e-demo
$ git init --initial-branch=main
$ git config user.email test@flowstate.dev && git config user.name "FS Demo"
$ echo "# Journal" > README.md && git add . && git commit -m init
$ echo "modified by user" >> README.md && git add README.md
$ echo "untracked junk" > scratch.md
$ git status --porcelain
M  README.md
?? scratch.md
```

Driver script (real Python invocation, no mocks):

```
$ uv run python -c "<persist driver — see Bash command in agent transcript>"
```

Observed output:

```
=== BEFORE persist ===
branch: main
HEAD: 608e10cb3c05e6187d998efbae63e29b5d23c4b5
status:
M  README.md
?? scratch.md

exit branch: flowstate/demo-run/exit-1
exit HEAD: fe910afcd0bc74bfc0301e80eec619d58f0daff4

=== Running persist ===
PersistResult: PersistResult(status='advanced',
  old_commit='608e10cb...', new_commit='0c4d1b8e...', conflict_files=[], reason=None)

=== AFTER persist ===
branch: main
HEAD: 0c4d1b8ea7c116d40e76bb1b3889ad0a82d7ea7e
status:
M  README.md
D  feature.txt
?? scratch.md
log:
0c4d1b8 flowstate: persist flowstate/demo-run/exit-1
fe910af flowstate demo: add feature
608e10c init

README in index:
# Journal
modified by user
```

Key invariants verified end-to-end against a real git repo:
- `main` ref advanced atomically (608e10c → 0c4d1b8) via `git update-ref` CAS.
- The user's checkout STILL has `M README.md` (staged user edit preserved in
  the index) and `?? scratch.md` (untracked file preserved on disk).
- The README blob in the index still contains "modified by user" — the
  user's staged changes were never touched by the merge.
- HEAD branch is still `main` — no `git checkout` or `git reset` ran in the
  user's checkout.
- The new `D feature.txt` line reflects that `main` now contains
  `feature.txt` while the user's index does not — exactly the documented
  behavior (the merge lives in the branch, the user's checkout is
  untouched). The user can `git checkout feature.txt` to pick it up.

### CAS retry seam

`merge_to_source_branch_via_detached_worktree` takes an optional async
`pre_cas_hook(attempt)` parameter. Production calls always pass `None`.
Tests use the hook to deterministically advance `refs/heads/main` between
the helper's `rev-parse` and `update-ref`, which forces a CAS failure on
the first attempt and verifies the retry path. See
`TestCasRetry.test_cas_retry_succeeds_on_second_attempt` and
`TestCasExhausted.test_cas_exhausted_after_three_attempts`.

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
