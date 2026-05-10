# [STATE-013] Add `source_branch` column to `flow_runs`

## Domain
state

## Status
todo

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
_[Agent fills this in: exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
