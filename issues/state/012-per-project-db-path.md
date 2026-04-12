# [STATE-012] Derive DB path from `Project.db_path`; drop hardcoded default

## Domain
state

## Status
done

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
- [x] `FlowstateDB.__init__` requires an explicit `db_path: Path | str` (no default). Tilde expansion is still supported for robustness. `:memory:` still works.
- [x] `FlowstateConfig.database_path` and `FlowstateConfig.database_wal_mode` are removed from the dataclass entirely. A legacy `[database]` section in an existing user TOML is **silently ignored** by `_parse_toml` (no error, no effect).
- [x] `app.py` / `create_app()` passes `project.db_path` to the `FlowstateDB` constructor as a `Path` (no more stringification).
- [x] `cli.py` and other direct `FlowstateDB(...)` construction sites are updated (5 callsites in `cli.py`, 1 in `server/app.py`).
- [x] All state and server test suites pass with no new regressions beyond the sprint's pre-existing failure set.
- [x] New unit test `test_two_projects_have_isolated_databases` verifies that two `FlowstateDB` instances on distinct paths do not share rows (TEST-5 at the unit level).
- [x] New unit test `test_accepts_path_object` verifies the constructor accepts `pathlib.Path` directly.

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

### Post-Implementation Verification

Two scratch projects spun up against two isolated `FLOWSTATE_DATA_DIR` roots. Each
produced its own per-project database under the correct slug. Observed output
reproduced verbatim below.

**Setup**:
```
$ rm -rf /tmp/fs-state-a /tmp/fs-state-b /tmp/fs-state-data
$ mkdir -p /tmp/fs-state-a/flows /tmp/fs-state-b/flows
$ printf '[server]\nhost="127.0.0.1"\nport=9093\n[flows]\nwatch_dir="flows"\n' > /tmp/fs-state-a/flowstate.toml
$ printf '[server]\nhost="127.0.0.1"\nport=9094\n[flows]\nwatch_dir="flows"\n' > /tmp/fs-state-b/flowstate.toml
```

**Project A** (port 9093):
```
$ cd /tmp/fs-state-a && FLOWSTATE_DATA_DIR=/tmp/fs-state-data \
    nohup uv --project <worktree> run flowstate server > /tmp/fs-state-a.log 2>&1 &
# PID=60108, sleep 4s

$ curl -s http://127.0.0.1:9093/api/runs
[]

$ find /tmp/fs-state-data/projects -name flowstate.db
/tmp/fs-state-data/projects/fs-state-a-1c081023/flowstate.db

# Log tail:
Starting Flowstate server on 127.0.0.1:9093
Project: /private/tmp/fs-state-a (slug=fs-state-a-1c081023)
INFO:     Started server process [60110]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9093
INFO:     127.0.0.1:52559 - "GET /api/runs HTTP/1.1" 200 OK
```

Exactly one DB exists, under the project-A slug. No `~/.flowstate/flowstate.db`
was written (the legacy path is nonexistent in the isolated `FLOWSTATE_DATA_DIR`).

**Project B** (port 9094, after killing server A):
```
$ kill 60108
$ cd /tmp/fs-state-b && FLOWSTATE_DATA_DIR=/tmp/fs-state-data \
    nohup uv --project <worktree> run flowstate server > /tmp/fs-state-b.log 2>&1 &
# PID=60143, sleep 4s

$ curl -s http://127.0.0.1:9094/api/runs
[]

$ find /tmp/fs-state-data/projects -name flowstate.db
/tmp/fs-state-data/projects/fs-state-a-1c081023/flowstate.db
/tmp/fs-state-data/projects/fs-state-b-3d4ed518/flowstate.db

# Log tail:
Starting Flowstate server on 127.0.0.1:9094
Project: /private/tmp/fs-state-b (slug=fs-state-b-3d4ed518)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9094
INFO:     127.0.0.1:52561 - "GET /api/runs HTTP/1.1" 200 OK
```

**Conclusion**:
- Two distinct DB files exist at `fs-state-a-1c081023/flowstate.db` and
  `fs-state-b-3d4ed518/flowstate.db`, one per project slug.
- Both slugs are stable hashes derived from each project's resolved root path.
- `GET /api/runs` on each server returns `[]` independently — neither project
  sees the other's runs.
- No database file was created at the legacy `~/.flowstate/flowstate.db` path.
- Sprint contract TEST-5 (DB isolation between two projects) is demonstrated
  at both the unit level (new `test_two_projects_have_isolated_databases`) and
  the real-server level (above).

### Unit-level Test Run
```
$ uv run pytest tests/state/ tests/server/test_config.py tests/server/test_app.py -q
257 passed in 0.92s
```

### Lint / Type Check
```
$ uv run ruff check src/flowstate/state/ src/flowstate/config.py \
    src/flowstate/server/app.py src/flowstate/cli.py tests/state/ \
    tests/server/test_app.py tests/server/test_integration.py
All checks passed!

$ uv run pyright src/flowstate/
0 errors, 0 warnings, 0 informations
```

## Completion Checklist
- [x] `FlowstateDB` constructor requires explicit path
- [x] Default `database_path` + `database_wal_mode` removed from config schema
- [x] `app.py` wiring updated (passes `Path` directly, no stringification)
- [x] `cli.py` wiring updated (5 callsites)
- [x] Isolation unit test added (`test_two_projects_have_isolated_databases`)
- [x] Path-object acceptance test added (`test_accepts_path_object`)
- [x] `/test` passes (257/257 in scope; 6 pre-existing failures unchanged)
- [x] `/lint` passes on all files in scope
- [x] E2E two-project isolation verified against real running servers
