# Evaluation: ENGINE-088

**Date**: 2026-05-10
**Sprint**: sprint-037 (Phase 37c)
**Verdict**: PASS

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | Issue file contains a populated "E2E Verification Log" section with concrete commands, observed output, and conclusions. |
| Commands are specific and concrete | PASS | Exact `pytest` invocations, exact `git` commands in the demo driver, exact commit SHAs and `PersistResult` repr. |
| Real E2E (no mocks/TestClient) | PASS | The tests in `test_persist_exit_worktree.py` operate on real `tmp_path` git repos with real `git` subprocesses. The demo driver runs against a real `/tmp/flowstate-e2e-demo` repo. No TestClient/mock harness. |
| Scenarios cover acceptance criteria | PASS | 17 unit tests cover capture-source-branch (3), happy path, dirty-WT-untouched, conflict, CAS retry, CAS exhaustion, lock serialization, helper skip cases, and executor-level integration including the conflict path. |
| Server restarted after changes | N/A | This is a library-level helper; verified directly via the public Python API. Executor integration test exercises the new `_complete_flow` await path. |
| Reproduction logged before fix (bugs) | N/A | Feature, not a bug. |

## Independent Verification

I built and ran four independent test drivers using the public `flowstate.engine.worktree.merge_to_source_branch_via_detached_worktree` API (and the public `flowstate.state.database.FlowstateDB` API for migration). I did not read source files except to confirm public symbols / argument names and to confirm the production call site does not bypass the seam.

### Test 1: Dirty working tree preserved (the load-bearing case)

**Setup** at `/tmp/flowstate-eval-dirty-wt`:
- `git init --initial-branch=main`, single initial commit on `main` (`7c33ea0`)
- Created exit branch `flowstate/eval/exit-1` with one commit adding `feature.txt`
- Made user's WT dirty: staged a modification to `README.md` ("user-modified content"), created untracked `scratch.md`

**Call**: `await merge_to_source_branch_via_detached_worktree(original_workspace=REPO, source_branch="main", exit_branch="flowstate/eval/exit-1")`

**Observed**:
```
PersistResult(status='advanced', old_commit='7c33ea0…', new_commit='b98a327…', conflict_files=[], reason=None)
BEFORE                                AFTER
branch: main                          branch: main           ← unchanged
status: M  README.md                  status: M  README.md   ← staged work preserved
        ?? scratch.md                         D  feature.txt  ← documented (main has it, index doesn't — user can `git checkout feature.txt`)
                                              ?? scratch.md   ← untracked preserved
index entry: 100644 6f7be07ffa…       index entry: 100644 6f7be07ffa…   ← BYTE-IDENTICAL
staged content: 'user-modified content'  staged content: 'user-modified content'  ← BYTE-IDENTICAL
```

Load-bearing invariants verified:
- The branch did NOT switch (still `main`); no `git checkout` ran in user's workspace.
- The README index blob is byte-identical (same SHA `6f7be07`) — the user's staged work was not touched.
- The staged content is byte-identical — confirmed via `git show :README.md`.
- The untracked `scratch.md` survived on disk with the same content.
- The `main` ref advanced atomically via `git update-ref`; `main^1` is C0 (init) and `main^2` is C1 (feature commit), confirming a real merge commit with two parents, satisfying TEST-37c.8's structural requirement.

Note: my initial driver mistakenly asserted that `rev-parse HEAD` should stay constant. `HEAD` is a symbolic ref to `refs/heads/main`; when `main` advances via `update-ref`, `rev-parse HEAD` reports the new commit by definition. The user's working tree and index are what matters, and both are untouched. The "user checkout never trampled" guarantee holds.

### Test 2: Conflict path preserves exit branch and source ref

Setup at `/tmp/flowstate-eval-conflict`: both `main` and `flowstate/eval/exit-1` modify line 2 of `file.txt` differently.

Observed: `PersistResult(status='conflict', old_commit='842865a…', conflict_files=['file.txt'])`. `main` ref unchanged. Exit branch still listed in `git branch --list`. PASS.

### Test 3: CAS retry seam is wired into production path

Setup at `/tmp/flowstate-eval-cas`: invoked the helper with a `pre_cas_hook` that, on attempt 0, makes an external commit on `main` (different file from the exit branch). 

Observed: `Hook invoked on attempts: [0, 1]`. Final `PersistResult(status='advanced', ...)`. The hook was called twice (once forcing CAS failure, once allowing success). 

Production wiring confirmed: executor.py:3389 calls `merge_to_source_branch_via_detached_worktree(...)` without passing `pre_cas_hook` — it defaults to `None`. The test-only seam IS on the same code path production uses; it just no-ops in production. The seam is not bypassed.

### Test 4: File lock serializes concurrent persists

Setup at `/tmp/flowstate-eval-lock`: two concurrent `asyncio.gather`-ed calls targeting the same workspace, each with a `pre_cas_hook` that sleeps 0.5s inside the critical section.

Observed timeline:
```
+0.000s  A_call_start
+0.001s  B_call_start
+0.044s  A_enter_cas_window_attempt0
+0.546s  A_exit_cas_window_attempt0
+0.565s  A_call_done_status=advanced
+0.615s  B_enter_cas_window_attempt0   ← B waited 0.614s from start; only entered after A finished
+1.117s  B_exit_cas_window_attempt0
+1.156s  B_call_done_status=advanced
```

Zero overlap. B entered its CAS window 70ms *after* A's call fully completed — proving the lock blocked B until A released. Both calls returned `status='advanced'`. The resulting `git log --oneline main` shows both exit branches' commits chained in `main` (no lost work). PASS.

### Test 5: Helper skip cases do not raise

Both "exit branch missing" and "source branch missing" cases return `PersistResult(status='skipped', reason='exit_branch_missing')` cleanly without raising. PASS.

### Test 6: Migration 3 accepts `completed_with_conflicts`

Initialized a fresh `FlowstateDB`. Verified:
- `flow_runs` schema includes the `source_branch` column (from STATE-013).
- Inserting a row with `status='completed_with_conflicts'` succeeds.
- Inserting a row with `status='bogus_status'` fails with `CHECK constraint failed: status IN ('created', 'running', 'pausing', 'paused', 'completed', 'failed', 'cancelled', 'budget_exceeded', 'completed_with_conflicts')` — confirming the CHECK was widened, not removed.

### Lint, types, test suite

- `uv run ruff check src/flowstate/engine/ src/flowstate/state/ tests/engine/test_persist_exit_worktree.py` — All checks passed.
- `uv run pyright src/flowstate/engine/ src/flowstate/state/ tests/engine/test_persist_exit_worktree.py` — 0 errors, 0 warnings.
- `uv run pytest tests/engine/test_persist_exit_worktree.py -v` — 17/17 PASS in 2.21s.
- `uv run pytest tests/engine/ --ignore=tests/engine/test_executor.py -q` — 490 PASS (pre-existing TestContextModeHandoff hang documented in issue).
- `uv run pytest tests/state/ tests/dsl/ -q` — 675 PASS.
- `uv run pytest tests/engine/test_events.py -v` — 33/33 PASS including `test_event_type_count` (count is 23, +2 for the new events). 

## Criteria Results (Sprint Contract TEST-37c.6 through TEST-37c.16)

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| TEST-37c.6 | Source branch captured at run-start | PASS | `TestCaptureSourceBranch::test_returns_branch_name` covers the capture function. Integration test `test_persist_advances_source_branch_via_executor` exercises the run-start → DB write path. |
| TEST-37c.7 | Detached HEAD records NULL source_branch | PASS | `TestCaptureSourceBranch::test_detached_head_returns_none` confirms helper returns None for detached HEAD; integration tests show NULL source_branch leads to a clean `skipped` persist. |
| TEST-37c.8 | Successful merge advances source branch | PASS | My independent dirty-WT driver shows `main` advanced to a merge commit `b98a327…` with `main^1` = C0 and `main^2` = C1. Real two-parent merge. |
| TEST-37c.9 | User's working tree undisturbed | PASS | Independent driver verified: branch unchanged (still `main`), staged README blob byte-identical (SHA `6f7be07`), untracked `scratch.md` preserved on disk. Test `TestWorkingTreeUntouched::test_dirty_working_tree_preserved` also covers this. |
| TEST-37c.10 | Conflict marks `completed_with_conflicts`, preserves exit branch | PASS | My independent conflict driver verified `status='conflict'`, conflict_files=`['file.txt']`, main unchanged, exit branch preserved. Integration test `test_persist_conflict_preserves_exit_branch_via_executor` exercises the full DB-update path including the new status. |
| TEST-37c.11 | CAS retry succeeds when source moves | PASS | `TestCasRetry::test_cas_retry_succeeds_on_second_attempt` plus my independent driver confirmed the `pre_cas_hook` seam is invoked between `rev-parse` and `update-ref` on the same code path production uses. |
| TEST-37c.12 | CAS exhaustion preserves exit branch | PASS | `TestCasExhausted::test_cas_exhausted_after_three_attempts` PASSED. |
| TEST-37c.13 | File lock serializes concurrent runs | PASS | Independent driver timeline shows zero overlap: B entered its CAS window only 70ms after A's call fully completed (B waited ~0.6s for the lock). Both succeeded with chained merge commits in `main`. |
| TEST-37c.14 | `worktree_persist = false` flows behave unchanged | PASS | All 490 pre-existing engine tests pass (no regression). The persist branch in `_complete_flow` is gated on `flow.worktree_persist`. |
| TEST-37c.15 | Skip cases do not error and produce a reason | PASS | All four skip cases covered: `test_persist_skips_when_no_source_branch_recorded`, `test_persist_skips_when_no_exit_task`, `test_persist_skips_when_exit_task_has_no_worktree_artifact`, `test_persist_skips_when_exit_task_reached_via_context_none`. My independent driver also exercised exit_branch_missing → clean skip. |
| TEST-37c.16 | Two new event types registered | PASS | `EventType.SOURCE_BRANCH_ADVANCED` and `EventType.SOURCE_BRANCH_PERSIST_CONFLICT` present; total count is 23 (+2 from prior 21); `test_event_type_count` passes. |

## Out-of-Scope Note

The orchestrator note flagged that ENGINE-088 added Migration 3 to state-domain files (`state/schema.sql`, `state/database.py`, `state/repository.py`) to allow `completed_with_conflicts` in the `flow_runs.status` CHECK constraint. I verified this works: the new status is accepted, the old statuses still validate, and bogus values are still rejected. The 675 state+dsl tests pass with no regression. The cross-domain addition is sound.

## Failures

None.

## Summary

11 of 11 sprint contract tests for ENGINE-088 PASS. The 17-test unit suite passes. Independent verification using only public APIs confirms:
- The load-bearing dirty-WT invariant holds (user's index byte-identical, untracked file preserved, branch never switched).
- The conflict path preserves the source ref and exit branch.
- The CAS retry seam is on the same code path production uses and is correctly wired (production passes `None` for the hook).
- The file lock truly serializes concurrent persists (verified with timestamps showing zero overlap).
- Migration 3 correctly widens the CHECK constraint without breaking it.

Lint and type checks are clean. No regressions in engine/state/dsl test suites. The cross-domain Migration 3 addition is intentional and verified.

**Verdict: PASS.**
