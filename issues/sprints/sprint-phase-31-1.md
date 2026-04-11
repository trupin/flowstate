# Sprint Phase 31.1 — Project-rooted runtime

**Issues**: SERVER-026, STATE-012, SERVER-027, ENGINE-079, ENGINE-080
**Domains**: server, state, engine
**Date**: 2026-04-11
**Depends on**: SHARED-006 (spec), SHARED-007 (`Project` dataclass + `resolve_project()`)

## Batch Goal

Prove the **working-directory invariant**: the Flowstate server must start, serve the UI, and successfully execute a flow when launched from an **arbitrary directory** that contains a `flowstate.toml` — with **zero CWD-relative path assumptions** anywhere in the pipeline (config lookup, DB path, flows directory, workspace resolution, auto-generated workspaces).

By end of sprint, a contributor or end user must be able to:

1. `cd` into any directory with a `flowstate.toml`.
2. Run `uv run flowstate server` (or `flowstate server` once installed).
3. Open the UI, see flows from that project's `flows/` directory.
4. Trigger a flow that uses either an explicit `workspace` (relative to the flow file) or no workspace (auto-generated under the project's per-project data dir).
5. Observe that the run's state, logs, and workspace all live under `~/.flowstate/projects/<slug>/`, not under any global or CWD-relative location.

## Acceptance Tests (batch-level, E2E)

These tests run against the **real running server**, exercising the full stack. Per-issue unit tests are described in each issue file — the tests below are what the evaluator will verify for the sprint as a whole.

### TEST-1: Server starts from dev repo (contributor regression)
  Given: Fresh checkout of the Flowstate dev repo with the new committed `flowstate.toml` at repo root
  When: `uv run flowstate server` is launched from the repo root
  Then: Server starts without error, `GET /api/flows` returns a JSON array (may be empty), and the UI at `/` loads successfully

### TEST-2: Server refuses to start outside any project
  Given: A directory with no `flowstate.toml` in it or any ancestor (e.g. `/tmp/nowhere`)
  When: `flowstate server` is launched from that directory with no `FLOWSTATE_CONFIG` set
  Then: Process exits non-zero within 5 seconds and stderr contains a clear "No flowstate.toml found" message

### TEST-3: Server starts from an arbitrary scratch project
  Given: A fresh scratch project at `/tmp/fs-sprint-a/` containing a minimal `flowstate.toml` with `[flows] watch_dir = "flows"` and a `flows/` directory
  When: `flowstate server` is launched from `/tmp/fs-sprint-a/`
  Then: Server starts successfully, the project's data directory `~/.flowstate/projects/fs-sprint-a-*/` is created, and `GET /api/flows` responds with `[]`

### TEST-4: Flow drop-in is picked up from the project's flows_dir
  Given: The running server from TEST-3
  When: A valid `.flow` file is written to `/tmp/fs-sprint-a/flows/demo.flow`
  Then: Within the watcher debounce window, `GET /api/flows` returns a list containing `demo`, and the UI's flow list shows it

### TEST-5: DB isolation between two projects
  Given: Two scratch projects `/tmp/fs-sprint-a/` and `/tmp/fs-sprint-b/`, each with its own `flowstate.toml` and a flow triggered at least once
  When: The sprint verifier inspects `~/.flowstate/projects/fs-sprint-a-*/flowstate.db` and `~/.flowstate/projects/fs-sprint-b-*/flowstate.db`
  Then: Both DB files exist at distinct paths, and the `runs` table of each DB contains only the runs triggered from its own project (no cross-contamination). No database file is created at the legacy `~/.flowstate/flowstate.db` path.

### TEST-6: Flow-relative workspace resolution (CWD-independent)
  Given: Project at `/tmp/fs-sprint-a/` with `flows/demo.flow` declaring `workspace = "../target"`, and an existing git repo at `/tmp/fs-sprint-a/target/`
  When: The server is launched with `FLOWSTATE_CONFIG=/tmp/fs-sprint-a/flowstate.toml` from a **different** CWD (e.g. `/`), and the flow is triggered via `POST /api/runs`
  Then: The run's execution context reports the resolved workspace as `/tmp/fs-sprint-a/target` (absolute, based on the flow file's parent, NOT the launching CWD), and the run reaches a terminal state without a `CwdResolutionError`

### TEST-7: Auto-generated workspace lives under the project's workspaces_dir
  Given: Project at `/tmp/fs-sprint-a/` with a flow `demo.flow` that has **no** explicit `workspace`
  When: The flow is triggered and reaches at least one executing node
  Then: The run's workspace directory exists under `~/.flowstate/projects/fs-sprint-a-*/workspaces/demo/<run-id-prefix>/`, is initialized as a git repo, and **no** directory is created under the legacy `~/.flowstate/workspaces/` path

### TEST-8: Same flow name in two projects does not collide
  Given: Projects `/tmp/fs-sprint-a/` and `/tmp/fs-sprint-b/`, each containing a `flows/demo.flow` (same flow name)
  When: Both servers (or the same server started against each in turn) trigger a run of `demo`
  Then: The two runs' auto-generated workspaces live under distinct paths — `~/.flowstate/projects/fs-sprint-a-*/workspaces/demo/...` and `~/.flowstate/projects/fs-sprint-b-*/workspaces/demo/...` — and deleting `~/.flowstate/projects/fs-sprint-a-*/` leaves project B's data intact

### TEST-9: FLOWSTATE_CONFIG override works from any CWD
  Given: A project at `/tmp/fs-sprint-a/` and a shell in `/` (no project here)
  When: `FLOWSTATE_CONFIG=/tmp/fs-sprint-a/flowstate.toml flowstate server` is launched
  Then: Server starts normally, behaves as if launched from `/tmp/fs-sprint-a/`, and TEST-4 through TEST-7 still hold

### TEST-10: No stale CWD-relative plumbing remains
  Given: The migrated codebase
  When: `grep -rn` (or equivalent) searches for likely offenders — string literals `"./flows"`, `Path("flows")`, `Path.home() / ".flowstate" / "workspaces"`, and any direct read of `config.database.path` — in `src/flowstate/`
  Then: No production code path (excluding tests and the legacy-migration shim if any) still uses those patterns. `FlowstateDB()` cannot be constructed without an explicit `db_path`.

## Out of Scope

- **`flowstate init` command** — separate issue in Phase 31.2 (SERVER-028+).
- **Pretty error UX** for missing/invalid `flowstate.toml` — only the raw `ProjectNotFoundError` message is required this sprint (SERVER-029 owns prettification).
- **Env-var overrides** beyond `FLOWSTATE_CONFIG` — later in Phase 31.2.
- **Packaging / pipx distribution** — Phase 31.3.
- **Data migration** from the legacy `~/.flowstate/flowstate.db` / `~/.flowstate/workspaces/` layout — spec §13.3 says "greenfield; leave old data alone".
- **Multi-project UI switcher** — the server is bound to exactly one project per process this sprint.
- **Schedules and triggers CLIs** — migrated to `resolve_project()` in SERVER-026 but not independently verified in the E2E criteria above.
- **UI changes** — the UI is expected to work unchanged via the existing API shape.

## Integration Points

The `Project` dataclass from SHARED-007 (`src/flowstate/config.py`) is the single contract all issues consume. Its shape is fixed; no issue in this sprint may extend it:

```
Project:
    slug: str
    root: Path                 # project root (dir containing flowstate.toml)
    data_dir: Path             # ~/.flowstate/projects/<slug>/
    flows_dir: Path            # absolute, resolved from config.watch_dir
    db_path: Path              # data_dir / "flowstate.db"
    workspaces_dir: Path       # data_dir / "workspaces"
    config: FlowstateConfig    # parsed TOML (no [database] section)
```

**Producer → Consumer map for the batch**:

- **SERVER-026** establishes the plumbing:
  - `cli.py` commands call `resolve_project()` and pass the `Project` to `create_app(project)`.
  - `create_app()` takes `project: Project` as its primary argument.
  - Commits a `flowstate.toml` at the Flowstate dev-repo root.
- **STATE-012** consumes `project.db_path`:
  - `FlowstateDB.__init__` requires an explicit `db_path: Path` (no default).
  - `create_app()` wires `FlowstateDB(project.db_path)`.
  - `FlowstateConfig.database.path` is removed from the TOML schema.
- **SERVER-027** consumes `project.flows_dir`:
  - `FlowRegistry.__init__` takes absolute `flows_dir: Path` (no CWD-relative string).
  - `create_app()` wires `FlowRegistry(project.flows_dir)`.
- **ENGINE-079** consumes `project` + the **flow file path** from `FlowRegistry`:
  - `Project` and the flow file path are threaded through `create_app → QueueManager → Executor → ExecutionContext`.
  - `RegisteredFlow` exposes the absolute flow file path.
  - `resolve_workspace()` / `resolve_node_cwd()` helpers resolve relative paths against the flow file's parent directory, never CWD.
- **ENGINE-080** consumes `project.workspaces_dir`:
  - When `resolve_workspace()` returns `None`, the fallback is `project.workspaces_dir / flow_name / run_id[:8]`.
  - `queue_manager.py` and `routes.py` no longer reference `Path.home() / ".flowstate" / "workspaces"`.

**Wiring order inside `create_app(project)`** (all issues must agree on this sequence):

1. Construct `FlowstateDB(project.db_path)`.
2. Construct `FlowRegistry(project.flows_dir)`.
3. Construct `QueueManager(project=project, db=db, registry=registry, ...)`.
4. Construct `Executor` such that `ExecutionContext` receives `project` **and** the `flow_file: Path` for each run.

## Sequencing Within the Sprint

SERVER-026 must land first (it defines the `create_app(project)` signature everyone else wires into). The other four issues can proceed in parallel after SERVER-026, with one data dependency: **ENGINE-080 depends on ENGINE-079** (the auto-gen fallback is only reachable once `resolve_workspace()` returns `None` from the new helper).

Recommended parallel dispatch:
- Wave 1: SERVER-026 (solo).
- Wave 2 (parallel): STATE-012, SERVER-027, ENGINE-079.
- Wave 3: ENGINE-080 (after ENGINE-079).

## Done Criteria

This sprint is complete when:

- All 10 acceptance tests above pass against a freshly restarted server (no stale process).
- Every per-issue acceptance checklist is checked off in the issue files.
- Every issue's **E2E Verification Log** section is filled in with the exact commands run and observed output, including at least one run launched from a CWD **outside** the project root (proving the invariant).
- `/test` passes with no regressions (including the new isolation tests from STATE-012 and ENGINE-080).
- `/lint` passes (ruff + pyright clean).
- UI lint + build still pass (`cd ui && npm run lint && npm run build`).
- The committed `flowstate.toml` at the Flowstate dev-repo root is present so contributor workflows still work unchanged.
- Evaluator verdict is PASS against this contract.

## Risks & Concerns

- **Test fixture churn**: Many existing server/engine tests patch `load_config()` or construct `FlowstateDB()` with no args. SERVER-026 and STATE-012 must land a shared `project_fixture(tmp_path)` helper early to keep the test migration tractable; otherwise each of the three parallel wave-2 agents will reinvent it and conflict.
- **`RegisteredFlow` shape change**: ENGINE-079 requires the flow file's absolute path on `RegisteredFlow`. If that field isn't already present, SERVER-027 should add it while it's editing `FlowRegistry`, so ENGINE-079 doesn't have to re-edit the same file.
- **Restart discipline**: TEST-6, TEST-7, and TEST-9 only prove the invariant if the evaluator restarts the server between CWD changes. Domain agents must explicitly document server PID / restart commands in their E2E Verification Logs.
- **Legacy data**: Developers running this sprint locally may already have `~/.flowstate/flowstate.db` and `~/.flowstate/workspaces/` from prior installs. The sprint is greenfield — leave those alone, but evaluator should assert no **new** writes go there.
- **Same-name project slugs**: Two different `/tmp/fs-sprint-a/` directories on the same machine (e.g., across worktrees) may produce colliding slugs. SHARED-007's slug derivation uses a path hash suffix, so this should be safe; TEST-8 depends on that behavior.
