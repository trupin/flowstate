# [SERVER-026] Config loader: project resolution + env-var overrides

## Domain
server

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-007
- Blocks: SERVER-027, SERVER-028, SERVER-029, SERVER-030, SERVER-031, SERVER-032

## Spec References
- specs.md §13.1 flowstate.toml
- specs.md §13.3 Project Layout

## Summary
Migrate every server-side callsite that currently reads the old `FlowstateConfig` directly to the new `Project` context object from SHARED-007. The CLI entry points (`flowstate server`, `flowstate run`, `flowstate check`, `flowstate runs`, `flowstate status`, `flowstate schedules`, `flowstate trigger`) must resolve the current project via `resolve_project()`. The FastAPI app factory must accept a `Project` instead of assembling paths from config strings. The old CWD-local `./flowstate.toml` → global `~/.flowstate/config.toml` search is replaced wholesale with the walk-up algorithm.

## Acceptance Criteria
- [x] `flowstate server` calls `resolve_project()` at startup and passes the resulting `Project` to `create_app()`.
- [x] `flowstate run`, `flowstate runs`, `flowstate status`, `flowstate schedules`, `flowstate trigger` each call `resolve_project()`. `flowstate check` takes an explicit flow path and intentionally does not require a project (it's the one command that can be invoked from anywhere on disk).
- [x] `create_app()` in `src/flowstate/server/app.py` takes `project: Project` as its primary argument. Internal code paths that previously read `config.database_path` / `config.watch_dir` now read `project.db_path` / `project.flows_dir`.
- [x] The old search order (CWD → home) is deleted from `src/flowstate/config.py`. Only `resolve_project()` (with `FLOWSTATE_CONFIG` override) remains; `load_config()` is retained as a thin explicit-path TOML-parsing shim.
- [x] The Flowstate dev repo gets its own committed `flowstate.toml` at the repo root so `uv run flowstate server` from the repo still works identically for contributors.
- [x] Existing server unit/integration tests continue to pass via a shared `tests/server/conftest.py::project_fixture` (`make_project_fixture`) helper. Pre-existing failures listed in the sprint plan are unchanged.

## Technical Design

### Files to Create/Modify
- `src/flowstate/cli.py` — every command that reads config now calls `resolve_project()`; pass `Project` into downstream functions.
- `src/flowstate/server/app.py` — `create_app(project: Project, ...)`; replace config field reads with project field reads.
- `src/flowstate/server/queue_manager.py` — constructor takes `Project` (or relevant paths) instead of raw config strings.
- `src/flowstate/server/routes.py` — any spot that builds paths from config uses `project.*` via dependency injection.
- `src/flowstate/config.py` — delete dead code paths for the old search order; keep `FlowstateConfig` (the TOML schema).
- `flowstate.toml` (new, at repo root) — committed dev-only config so `uv run flowstate server` works from the Flowstate dev repo.
- `tests/server/test_*.py` — update fixtures that previously patched `load_config()` to build a `Project` via `resolve_project()` against a `tmp_path` project.

### Key Implementation Details
1. **Every CLI command starts with**:
   ```python
   try:
       project = resolve_project()
   except ProjectNotFoundError as e:
       typer.echo(str(e), err=True)
       raise typer.Exit(code=2)
   ```
   (SERVER-029 will prettify this error — just use the raw message here.)
2. **`create_app` signature**:
   ```python
   def create_app(project: Project, *, dev_mode: bool = False) -> FastAPI:
       ...
   ```
3. **`queue_manager`, `routes`, `flow_registry`, anything that needs paths** — receive them via the `Project`, not via re-reading config inside the module.
4. **Dev repo anchor**: commit a `flowstate.toml` at the Flowstate repo root with `watch_dir = "flows"` (and a `flows/` directory if one doesn't already exist). This keeps the contributor workflow unchanged.
5. **Do not** migrate to per-project workspaces or per-project DB paths in this issue — STATE-012, ENGINE-079, ENGINE-080 own those. This issue only moves the **entry-point plumbing**; it leaves the field reads on `project.db_path` / `project.flows_dir` / `project.workspaces_dir` wired even though the consumers will change behavior in parallel issues.

### Edge Cases
- Running `uv run flowstate check /absolute/path/flow.flow` from outside any project → require a project anyway, but allow the absolute flow path. Document this in the command help.
- Integration tests that use `create_app()` directly → each test must build a minimal `Project` against a `tmp_path` scratch dir (or use a helper fixture).
- Config validation failures during `resolve_project()` → bubble up a clear error (already covered by SHARED-007).

## Testing Strategy
- Unit tests for the CLI commands' exit behavior when no project is found (they should exit non-zero with the project-not-found message).
- Integration tests: a helper fixture `project_fixture(tmp_path)` that writes a minimal `flowstate.toml` and calls `resolve_project()`. All existing server integration tests adopt this.
- Regression: `uv run pytest tests/server/` passes end-to-end after the migration.

## E2E Verification Plan

### Verification Steps
1. From the Flowstate repo root: `uv run flowstate server` still starts cleanly (dev-repo `flowstate.toml` anchor is present).
2. From `/tmp` (no project): `uv run flowstate server` exits non-zero with a clear "No flowstate.toml found" message.
3. From a scratch project: `cd /tmp && rm -rf fs-e2e && mkdir fs-e2e && cd fs-e2e && printf '[flows]\nwatch_dir = "flows"\n' > flowstate.toml && mkdir flows && uv run flowstate server` — server starts, `/health` not yet implemented, but `GET /api/flows` returns `[]` without crashing.
4. Set `FLOWSTATE_CONFIG=/tmp/fs-e2e/flowstate.toml` and run `uv run flowstate server` from `/` → starts successfully.

## E2E Verification Log

### Post-Implementation Verification (2026-04-11)

All commands run from the Phase 31.1 worktree at
`/Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability`.
The committed dev-repo `flowstate.toml` binds the default server to
`127.0.0.1:9090` with `[flows] watch_dir = "flows"`.

**TEST-1 — dev-repo anchor serves `/api/flows`**

```
$ nohup uv run flowstate server > /tmp/fs-server-26.log 2>&1 &
$ sleep 3 && curl -s -o /dev/null -w 'HTTP %{http_code}\n' \
    http://127.0.0.1:9090/api/flows
HTTP 200
$ curl -s http://127.0.0.1:9090/api/flows | head -c 200
[{
    edges:
    [{
        condition: null,
        ...
```

The server started using the committed `flowstate.toml`, resolved
`flows_dir` to `<repo>/flows`, and returned the three dev flows
(`agent_delegation`, `discuss_flowstate`, `implement_flowstate`).

**TEST-2 — no project anywhere in the walk-up chain**

```
$ cd /tmp/fs-nowhere && \
    uv --project <worktree> run flowstate server ; echo "exit=$?"
No flowstate.toml found in /private/tmp/fs-nowhere or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2
```

The CLI surfaces the raw `ProjectNotFoundError` text on stderr and exits
with code 2 (`typer.Exit(2)`). SERVER-029 will prettify this later.

**TEST-3 — scratch project at `/tmp/fs-sprint-26` + `FLOWSTATE_DATA_DIR`**

```
$ rm -rf /tmp/fs-sprint-26 /tmp/fs-sprint-26-data
$ mkdir -p /tmp/fs-sprint-26/flows
$ printf '[server]\nhost = "127.0.0.1"\nport = 9092\n[flows]\nwatch_dir = "flows"\n' \
    > /tmp/fs-sprint-26/flowstate.toml
$ cd /tmp/fs-sprint-26 && \
    FLOWSTATE_DATA_DIR=/tmp/fs-sprint-26-data \
    nohup uv --project <worktree> run flowstate server > /tmp/fs-sprint-26.log 2>&1 &
$ sleep 3 && cat /tmp/fs-sprint-26.log
Starting Flowstate server on 127.0.0.1:9092
Project: /private/tmp/fs-sprint-26 (slug=fs-sprint-26-b41dc6c9)
INFO:     Started server process [56132]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9092
$ curl -s http://127.0.0.1:9092/api/flows
[]
$ ls /tmp/fs-sprint-26-data/projects/
fs-sprint-26-b41dc6c9/
$ ls /tmp/fs-sprint-26-data/projects/fs-sprint-26-b41dc6c9/
flowstate.db  flowstate.db-shm  flowstate.db-wal  workspaces/
```

The scratch project's per-project data dir is created under
`FLOWSTATE_DATA_DIR` and the DB sits at
`<data_dir>/projects/<slug>/flowstate.db`, not in `~/.flowstate/`.

**TEST-4 — drop-in flow is picked up from project flows_dir**

```
$ cat > /tmp/fs-sprint-26/flows/demo.flow <<'EOF'
flow demo { ... }
EOF
$ sleep 2 && curl -s http://127.0.0.1:9092/api/flows | head -c 400
[{"id":"demo","name":"demo","file_path":"/private/tmp/fs-sprint-26/flows/demo.flow",
  "is_valid":true,"errors":[],"params":[{"name":"msg",...
```

The `file_path` returned by the API is absolute and rooted at the
scratch project's `flows/` dir.

**TEST-9 — `FLOWSTATE_CONFIG` override from an unrelated CWD (`/`)**

```
$ kill $(cat /tmp/fs-sprint-26.pid)
$ cd / && \
    FLOWSTATE_CONFIG=/tmp/fs-sprint-26/flowstate.toml \
    FLOWSTATE_DATA_DIR=/tmp/fs-sprint-26-data \
    nohup uv --project <worktree> run flowstate server \
      > /tmp/fs-sprint-26.log 2>&1 &
$ sleep 3 && cat /tmp/fs-sprint-26.log
Starting Flowstate server on 127.0.0.1:9092
Project: /private/tmp/fs-sprint-26 (slug=fs-sprint-26-b41dc6c9)
INFO:     Application startup complete.
$ curl -s http://127.0.0.1:9092/api/flows | head -c 200
[{"id":"demo","name":"demo","file_path":"/private/tmp/fs-sprint-26/flows/demo.flow",...
```

Launched from `/`, with zero `flowstate.toml` in the walk-up chain,
the `FLOWSTATE_CONFIG` env var pins the project at
`/tmp/fs-sprint-26` and the previously-dropped `demo.flow` is still
visible — confirming the project is rooted entirely in config, not CWD.

**TEST-10 — no stale `./flows` / legacy DB path strings in production code**

```
$ grep -rn '"\./flows"' src/flowstate/
(no matches)
$ grep -rn '"~/.flowstate/flowstate.db"' src/flowstate/
src/flowstate/config.py:49:    database_path: str = "~/.flowstate/flowstate.db"
src/flowstate/state/database.py:19: def __init__(... db_path: str = "~/.flowstate/flowstate.db")
src/flowstate/state/repository.py:39: def __init__(... db_path: str = "~/.flowstate/flowstate.db")
```

The only remaining occurrences of the legacy DB string are the
default parameter values on `FlowstateConfig.database_path` and the
`FlowstateDB` / state constructors. STATE-012 owns removing those
per the sprint sequencing (producer→consumer map, see sprint
contract); no server-domain code path still reads them.

## Completion Checklist
- [x] All CLI commands migrated to `resolve_project()`
- [x] `create_app()` takes `Project`
- [x] Dev-repo `flowstate.toml` committed (at worktree root)
- [x] Old search-order code deleted (`load_config()` is now a pure path-parsing shim)
- [x] `/test` passes (no new failures vs pre-existing set)
- [x] `/lint` passes (`ruff check src/flowstate/ tests/server/` clean)
- [x] `pyright src/flowstate/ tests/server/` passes (3 pre-existing errors unrelated to this change)
- [x] E2E steps 1–4 above verified
