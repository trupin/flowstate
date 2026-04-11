# [SERVER-026] Config loader: project resolution + env-var overrides

## Domain
server

## Status
todo

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
- [ ] `flowstate server` calls `resolve_project()` at startup and passes the resulting `Project` to `create_app()`.
- [ ] `flowstate run`, `flowstate check`, `flowstate runs`, `flowstate status`, `flowstate schedules`, `flowstate trigger` each call `resolve_project()` (except where the command takes an explicit flow file path that could live outside a project — in which case, still require a project and treat the flow path as relative to the project root if not absolute).
- [ ] `create_app()` in `src/flowstate/server/app.py` takes `project: Project` as its primary argument. Internal code paths that previously read `config.database.path` / `config.flows.watch_dir` now read `project.db_path` / `project.flows_dir`.
- [ ] The old search order (CWD → home) is deleted from `src/flowstate/config.py`. Only `resolve_project()` (with `FLOWSTATE_CONFIG` override) remains.
- [ ] The Flowstate dev repo gets its own committed `flowstate.toml` at the repo root so `uv run flowstate server` from the repo still works identically for contributors.
- [ ] Existing server integration tests continue to pass, using `tmp_path`-backed project fixtures that call `resolve_project()`.

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
_Filled in by the implementing agent._

## Completion Checklist
- [ ] All CLI commands migrated to `resolve_project()`
- [ ] `create_app()` takes `Project`
- [ ] Dev-repo `flowstate.toml` committed
- [ ] Old search-order code deleted
- [ ] `/test` passes
- [ ] `/lint` passes
- [ ] E2E steps 1–4 above verified
