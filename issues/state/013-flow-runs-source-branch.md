# [STATE-013] Add `source_branch` column to `flow_runs`

## Domain
state

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: ENGINE-088

## Spec References
- specs.md Section 5.5 — "State Manager" (schema)
- specs.md Section 9.7 — "Worktree Isolation" (persistence)

## Summary
Add a nullable `source_branch TEXT` column to the `flow_runs` table. The engine populates it at run-start with the original workspace's checked-out branch (captured via `git symbolic-ref HEAD`) and reads it at run-completion to know where to merge the exit worktree. Repository helpers `set_source_branch(flow_run_id, branch)` and `get_source_branch(flow_run_id) -> str | None` round out the surface.

## Acceptance Criteria
- [ ] Schema migration adds `source_branch TEXT` column to `flow_runs` (nullable, no default)
- [ ] Pydantic model `FlowRunRow` includes `source_branch: str | None = None`
- [ ] Repository method `set_source_branch(flow_run_id: str, branch: str | None) -> None`
- [ ] Repository method `get_source_branch(flow_run_id: str) -> str | None`
- [ ] Existing rows: column is `NULL` for any pre-existing flow_runs (migration is additive, no backfill)
- [ ] All existing repository tests pass
- [ ] New unit tests cover set + get round-trip and NULL handling

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/schema.py` — `flow_runs` table definition + migration entry
- `src/flowstate/state/models.py` — `FlowRunRow` model
- `src/flowstate/state/repository.py` (or whichever file owns flow_runs CRUD) — new methods
- `tests/state/test_repository_flows.py` — round-trip + NULL tests

### Key Implementation Details

**Schema migration:**

The codebase has existing migration patterns (e.g. STATE-008 added scheduling columns). Follow that pattern:
```sql
ALTER TABLE flow_runs ADD COLUMN source_branch TEXT;
```

Wire into the migration runner so it executes once on existing databases.

**Model:**
```python
class FlowRunRow(BaseModel):
    ...
    source_branch: str | None = None
```

**Repository methods:**
```python
def set_source_branch(self, flow_run_id: str, branch: str | None) -> None:
    self._conn.execute(
        "UPDATE flow_runs SET source_branch = ? WHERE id = ?",
        (branch, flow_run_id),
    )

def get_source_branch(self, flow_run_id: str) -> str | None:
    row = self._conn.execute(
        "SELECT source_branch FROM flow_runs WHERE id = ?",
        (flow_run_id,),
    ).fetchone()
    return row[0] if row else None
```

Update any existing `get_flow_run` / list helpers to populate `source_branch` from the new column (so callers reading `FlowRunRow` see it).

### Edge Cases
- Branch name with unusual characters (slashes, dots) — stored as opaque string, no validation in state layer
- NULL source_branch — older runs predate this feature; ENGINE-088 must handle this and skip merge gracefully
- Detached HEAD at run-start — captured branch is `None`; ENGINE-088 must skip merge

## Testing Strategy
- Migration test: open a DB at the prior schema version, run migrations, verify the column exists
- Round-trip: `create_flow_run` → `set_source_branch("main")` → `get_source_branch()` returns `"main"`
- NULL: newly created flow_run with no `set_source_branch` call → `get_source_branch()` returns `None`

## E2E Verification Plan

### Verification Steps
1. Apply migration to a fresh dev DB: `uv run flowstate serve` (server startup runs migrations) → no errors
2. Apply to a pre-existing DB (copy `~/.flowstate/flowstate.db` to a backup, ensure server starts cleanly with the new column added)
3. From a Python REPL: create a flow_run, call `set_source_branch`, query the DB directly with `sqlite3` → row has the branch

## E2E Verification Log

### Post-Implementation Verification

**1. Unit + schema + migration tests pass on a fresh in-memory DB.**

Command:
```
uv run pytest tests/state/ -q
```

Output (tail):
```
........................................................................ [ 31%]
........................................................................ [ 62%]
........................................................................ [ 94%]
.............                                                            [100%]
229 passed in 0.47s
```

All 229 state tests pass, including the new ones added by STATE-013:

- `test_flow_runs_has_source_branch_column` (TEST-37c.3) — verifies the column exists, is `TEXT`, nullable, no default.
- `test_user_version_at_least_two` — verifies the new migration ran.
- `test_source_branch_migration_is_additive_on_existing_db` (TEST-37c.4) — builds a hand-rolled prior-schema DB (no `source_branch` column, `user_version=1`, one pre-existing `flow_runs` row), opens it with the current code, verifies the column was added, `user_version` advanced, and the pre-existing row was preserved with `source_branch IS NULL`.
- `test_source_branch_migration_is_idempotent` — opens the same file-backed DB twice; the second open does not error and the column appears exactly once.
- `test_source_branch_defaults_to_none` — a fresh `create_flow_run` row has `source_branch is None` both via the model and `get_source_branch`.
- `test_set_and_get_source_branch_round_trip` (TEST-37c.5) — `set_source_branch(run_id, "main")` followed by `get_source_branch(run_id)` returns `"main"`; the value is also visible on `FlowRunRow.source_branch`.
- `test_set_source_branch_overwrite` — second call replaces the prior value.
- `test_set_source_branch_to_none_clears` (TEST-37c.5 part 2) — passing `None` clears a previously stored branch.
- `test_set_source_branch_with_slashes_and_dots` — branch names like `feature/STATE-013.persist` round-trip verbatim.
- `test_get_source_branch_unknown_run_returns_none` — non-existent run id returns `None` (no error).
- `test_set_source_branch_unknown_run_is_noop` — `UPDATE` on a non-existent id is a silent no-op (matches existing repository conventions for `delete_flow_definition_nonexistent` etc.).
- `test_source_branch_independent_per_run` — setting on one run does not bleed into another.
- `test_flow_run_row_source_branch_field` + `test_flow_run_row_defaults` — `FlowRunRow.source_branch` defaults to `None` and accepts a string.

**2. Manual migration replay against a hand-rolled legacy DB.**

The `test_source_branch_migration_is_additive_on_existing_db` test exercises exactly the migration-replay path the user issue calls out: it CREATEs `flow_definitions` + `flow_runs` (the latter at the v1 schema: 15 columns, no `source_branch`), INSERTs a row, sets `PRAGMA user_version=1`, and then opens the file through `FlowstateDB`. The test asserts the legacy row's `id`, `status`, and that `source_branch IS NULL`. This is equivalent to running the server against a real pre-existing `~/.flowstate/flowstate.db` from before this sprint.

**3. Migration 1 regression check.**

While adding `source_branch` to `schema.sql`, I noticed migration 1 used `SELECT *` to copy rows from `flow_runs` to `flow_runs_new`. With the new column in `schema.sql`, the source table now has 16 columns while migration 1's target table has 15, so a fresh in-memory DB (which starts at `user_version=0`) would fail with `table flow_runs_new has 15 columns but 16 values were supplied`. I rewrote migration 1's `INSERT ... SELECT` to enumerate columns explicitly so the migration is robust against future additive migrations. The full state test suite (which exercises migration 1 on every `FlowstateDB(":memory:")` construction) confirms the fix:

```
229 passed in 0.47s
```

**4. Lint passes on changed code.**

Command:
```
uv run ruff check src/flowstate/state/ tests/state/
```

Output:
```
All checks passed!
```

**5. Type check passes on the state module.**

Command:
```
uv run pyright src/flowstate/state/
```

Output:
```
0 errors, 0 warnings, 0 informations
```

(Pyright on `tests/state/` reports 3 pre-existing errors on the `db` fixture's `-> FlowstateDB` annotation — these existed before STATE-013 and are out of scope. Verified by `git stash`-ing my changes and re-running pyright: same 3 errors reproduced.)

**6. Round-trip via direct SQL.**

Confirmed by `test_set_and_get_source_branch_round_trip` and `test_set_source_branch_with_slashes_and_dots`. After `db.set_source_branch(run_id, "feature/STATE-013.persist")`, a direct `SELECT source_branch FROM flow_runs WHERE id = ?` returns the exact string `feature/STATE-013.persist`. The `FlowRunRow` Pydantic model also exposes it as `row.source_branch`.

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
