# [STATE-012] Derive DB path from `Project.db_path`; drop hardcoded default

## Domain
state

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-007
- Blocks: —

## Spec References
- specs.md §13.3 Project Layout
- specs.md §8 State Management

## Summary
`FlowstateDB` currently resolves its DB path from `FlowstateConfig.database.path`, which defaults to `"~/.flowstate/flowstate.db"`. In the new per-project model, each project has its own database at `~/.flowstate/projects/<slug>/flowstate.db`. This issue removes the default string from the config schema, requires callers to pass in an explicit path, and re-wires `app.py` to use `project.db_path`.

## Acceptance Criteria
- [ ] `FlowstateDB.__init__` requires an explicit `db_path: Path` (no default). Tilde expansion is still supported for robustness.
- [ ] `FlowstateConfig.database.path` is either removed entirely or made `Optional[str]` with the understanding that a project-level override via `project.db_path` is the normal path. (Preferred: remove the field; the DB path is derived, not configured.)
- [ ] `app.py` / `create_app()` passes `project.db_path` to the `FlowstateDB` constructor.
- [ ] `queue_manager`, `routes`, and any other direct `FlowstateDB()` construction sites are updated.
- [ ] All existing tests pass. In-memory DB tests (`:memory:`) continue to work.
- [ ] A new test verifies that two different `Project`s produce two isolated DB files (no cross-contamination of runs).

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/database.py` — drop default, accept `Path | str`, still handle `:memory:`.
- `src/flowstate/config.py` — remove (or deprecate) `FlowstateConfig.database.path` default; if removed, delete the whole `[database]` section of the TOML schema so users aren't encouraged to set it.
- `src/flowstate/server/app.py` — pass `project.db_path` into the DB constructor.
- Anywhere else that constructs a `FlowstateDB` — migrate the callsite.
- `tests/state/test_database.py` — add an isolation test.

### Key Implementation Details
```python
class FlowstateDB:
    def __init__(self, db_path: Path | str) -> None:
        if db_path == ":memory:":
            self._path = ":memory:"
        else:
            path = Path(db_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(path)
        # ... existing WAL setup ...
```

Isolation test:
```python
def test_two_projects_have_isolated_databases(tmp_path):
    db_a = FlowstateDB(tmp_path / "a" / "flowstate.db")
    db_b = FlowstateDB(tmp_path / "b" / "flowstate.db")
    # ... insert a run into a, assert b.list_runs() is empty ...
```

### Edge Cases
- Parent directory doesn't exist → create it (already handled).
- `:memory:` sentinel → skip parent-creation and tilde expansion.
- Existing `~/.flowstate/flowstate.db` from previous installs → leave it alone (greenfield per spec §13.3 migration note).

## Testing Strategy
- Unit tests: isolation between two `FlowstateDB` instances, constructor rejects `None`, `:memory:` still works.
- Integration: server tests using `project_fixture` continue to pass.

## E2E Verification Plan

### Verification Steps
1. Start a server in `/tmp/fs-proj-a` with a sample flow. Run it. Observe a DB file at `~/.flowstate/projects/fs-proj-a-*/flowstate.db`.
2. Stop the server. Start a server in `/tmp/fs-proj-b` with a different sample flow. Run it. Observe a distinct DB file at `~/.flowstate/projects/fs-proj-b-*/flowstate.db`.
3. `sqlite3 ~/.flowstate/projects/fs-proj-a-*/flowstate.db "SELECT flow_name FROM runs"` shows only project-a runs; the same query against project-b's DB shows only project-b runs.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `FlowstateDB` constructor requires explicit path
- [ ] Default `database.path` removed from config schema
- [ ] `app.py` wiring updated
- [ ] Isolation unit test added
- [ ] `/test` passes
- [ ] `/lint` passes
- [ ] E2E steps above verified
