# Sprint Phase 31.2 — User-facing deployability polish

**Issues**: SERVER-028, SERVER-029, SERVER-030, SERVER-031
**Domains**: server (only)
**Date**: 2026-04-11
**Depends on**: Phase 31.1 (SERVER-026, SERVER-027, STATE-012, ENGINE-079, ENGINE-080 — all PASSED)

## Batch Goal

Phase 31.1 proved the **runtime invariant**: Flowstate obeys `flowstate.toml` as a per-project anchor and keeps all state under `~/.flowstate/projects/<slug>/`. Phase 31.2 is the **user-facing polish layer** that makes that runtime actually usable by a first-time, pipx-style user. By the end of this sprint, this full journey must work top-to-bottom with zero hand-holding:

```
uv tool install flowstate                    # (out of scope — assume installed)
cd ~/some-repo                               # user's existing project
flowstate init                               # scaffold flowstate.toml + flows/example.flow
flowstate check flows/example.flow           # validates the seeded example
flowstate server                             # starts on 127.0.0.1:9090, no warning
curl http://127.0.0.1:9090/health            # returns {status, version, project{slug, root}}
```

And the two obvious failure modes must fail **gracefully**:

```
cd /tmp/nowhere && flowstate server          # exit 2, clear message pointing at `flowstate init`
flowstate server --host 0.0.0.0              # loud multi-line stderr warning, then starts
```

No code changes outside the server domain. All four issues touch overlapping surface area (`cli.py`, `app.py`, new `init_templates/` package, new health router), so they **must run sequentially in a single server-dev agent** — see Sequencing below.

## Acceptance Tests (batch-level, E2E)

These tests run against the **real CLI and real running server**, not a `TestClient`. The evaluator will execute them from a fresh shell without reading source code. Per-issue unit tests are described in the individual issue files — the tests below are what the evaluator will verify for the sprint as a whole.

All tests assume `flowstate` is on PATH (installed via `uv run flowstate ...` or `uv tool install -e .` inside the worktree). Tests use scratch directories under `/tmp/` that do **not** share slugs with Phase 31.1 fixtures.

### TEST-1: `flowstate init` in an empty directory produces a working project

  Given: An empty scratch directory `/tmp/fs-init-empty/` (no `package.json`, `pyproject.toml`, or `Cargo.toml`)
  When: `flowstate init` is run from that directory
  Then:
  - Exit code is 0
  - `flowstate.toml` exists at `/tmp/fs-init-empty/flowstate.toml` and contains `host = "127.0.0.1"` and `port = 9090`
  - `flows/example.flow` exists at `/tmp/fs-init-empty/flows/example.flow`
  - Stdout mentions both files and prints a "Next:" block with `flowstate check flows/example.flow` and `flowstate server`
  - `flowstate check flows/example.flow` run in the same directory exits 0 (the seeded flow parses and type-checks)

### TEST-2: Project-type detection picks the Node template

  Given: A scratch directory `/tmp/fs-init-node/` containing `package.json` (valid JSON, e.g. `{"name":"demo"}`)
  When: `flowstate init` is run from that directory
  Then:
  - Exit code is 0
  - `flows/example.flow` contains an observable Node marker (a comment or node name mentioning `npm`, `node`, `install`, `build`, or `test` — the evaluator will grep for `npm` or `node`)
  - The template is distinguishable from the generic template produced in TEST-1 (diff is non-empty)
  - `flowstate check flows/example.flow` passes

### TEST-3: Project-type detection picks the Python template

  Given: A scratch directory `/tmp/fs-init-py/` containing `pyproject.toml` (minimal, e.g. `[project]\nname = "demo"\nversion = "0.1.0"`)
  When: `flowstate init` is run
  Then:
  - Exit code is 0
  - `flows/example.flow` contains a Python marker (grep for `pytest`, `ruff`, `pip`, or `python`)
  - `flowstate check flows/example.flow` passes

### TEST-4: Project-type detection picks the Rust template

  Given: A scratch directory `/tmp/fs-init-rust/` containing `Cargo.toml` (minimal, with `[package]` section)
  When: `flowstate init` is run
  Then:
  - Exit code is 0
  - `flows/example.flow` contains a Rust marker (grep for `cargo` or `rustc`)
  - `flowstate check flows/example.flow` passes

### TEST-5: `flowstate init` refuses to clobber existing `flowstate.toml`

  Given: `/tmp/fs-init-force/` already contains a `flowstate.toml` with a user-recognizable marker line (e.g. `# user-edited marker line`)
  When: `flowstate init` is run without `--force`
  Then:
  - Exit code is non-zero
  - Stderr mentions `flowstate.toml already exists` and `--force`
  - The existing `flowstate.toml` is **unchanged** (marker line still present, mtime unchanged)

### TEST-6: `flowstate init --force` overwrites only `flowstate.toml`, never the example flow

  Given: `/tmp/fs-init-force/` from TEST-5, plus an existing `/tmp/fs-init-force/flows/example.flow` containing a marker comment (e.g. `# user's hand-edited flow`)
  When: `flowstate init --force` is run
  Then:
  - Exit code is 0
  - `flowstate.toml` is regenerated (the user's marker line is gone)
  - `flows/example.flow` is **unchanged** — the user's marker comment is still present, mtime unchanged
  - Stdout contains a note about the preserved `flows/example.flow`

### TEST-7: `flowstate server` outside any project exits 2 with a friendly error

  Given: A directory with no `flowstate.toml` in CWD or any ancestor (e.g. `/tmp/fs-no-project/`, an empty dir), and no `FLOWSTATE_CONFIG` env var
  When: `flowstate server` is launched from that directory
  Then:
  - Process exits within 5 seconds with exit code **2** (not 1)
  - Stderr contains `No flowstate.toml found in /tmp/fs-no-project` and a pointer to `flowstate init`
  - Stderr does **not** contain a Python traceback (`Traceback (most recent call last):` is absent)

### TEST-8: Other project-requiring commands also fail cleanly outside a project

  Given: Same empty `/tmp/fs-no-project/` as TEST-7
  When: Each of the following is run in turn: `flowstate run flows/foo.flow`, `flowstate check flows/foo.flow`, `flowstate runs`, `flowstate status <fake-id>`
  Then:
  - Every invocation exits with code 2
  - Every stderr contains the same `No flowstate.toml found` message
  - No Python tracebacks appear

### TEST-9: `flowstate init` itself works outside any project

  Given: The same `/tmp/fs-no-project/` (no anchor)
  When: `flowstate init` is run
  Then:
  - Exit code is 0 (init bypasses the project-required check)
  - `flowstate.toml` and `flows/example.flow` are created at that path

### TEST-10: `flowstate --version` and `flowstate --help` work outside any project

  Given: `/tmp/fs-no-project/` (no anchor)
  When: `flowstate --version` and `flowstate --help` are each run
  Then:
  - Both exit 0
  - `--version` prints a version string (any non-empty string; the evaluator will not pin a specific value)
  - `--help` prints a usage block listing subcommands including `init`, `server`, `check`, `run`
  - No "No flowstate.toml found" message appears for either

### TEST-11: Default `flowstate server` does NOT print the host warning

  Given: A project at `/tmp/fs-polish-default/` created via `flowstate init` (TEST-1 style, generic template)
  When: `flowstate server` is launched from that directory with no `--host` flag
  Then:
  - Server starts and binds to `127.0.0.1:9090` (the default)
  - Stderr during startup does **not** contain the word `WARNING` or the `=====` border line
  - `GET http://127.0.0.1:9090/health` returns 200 (used as the readiness probe)

### TEST-12: `flowstate server --host 0.0.0.0` prints the loud warning and still starts

  Given: The same `/tmp/fs-polish-default/` project
  When: `flowstate server --host 0.0.0.0 --port 9091` is launched (port 9091 to avoid colliding with TEST-11)
  Then:
  - Stderr contains a multi-line banner with at least:
    - A row of `=` characters (border)
    - The substring `WARNING: Flowstate is binding to 0.0.0.0:9091`
    - The substring `NO AUTHENTICATION`
    - The substring `Only use non-loopback binds in trusted networks`
  - The warning appears **before** the server accepts its first connection
  - The server then starts normally and `GET http://127.0.0.1:9091/health` returns 200 (binding on `0.0.0.0` is reachable from loopback)
  - Warning is printed exactly once, not once per worker/reload

### TEST-13: `flowstate server --host 127.0.0.1` explicit does NOT warn

  Given: The same project
  When: `flowstate server --host 127.0.0.1 --port 9092` is launched explicitly
  Then:
  - Stderr contains no `WARNING` banner
  - Server starts and `/health` returns 200

### TEST-14: `GET /health` returns the correct shape and slug for the running project

  Given: A project created via `flowstate init` at `/tmp/fs-health-a/` and a running `flowstate server` launched from that directory on `127.0.0.1:9090`
  When: `curl -sS http://127.0.0.1:9090/health` is executed
  Then:
  - HTTP status is 200
  - Response is valid JSON
  - `status` equals `"ok"`
  - `version` is a non-empty string (source install is allowed to return `"0.0.0+dev"`; the evaluator will accept either a PEP 440 version or that sentinel)
  - `project.slug` starts with `fs-health-a-` (slug derivation from Phase 31.1 — path hash suffix)
  - `project.root` is an **absolute path** ending in `fs-health-a` and matches the directory the server was launched from
  - No authentication headers are required; the raw `curl` call succeeds

### TEST-15: `/health` reflects the project switch when the server is restarted elsewhere

  Given: TEST-14's server has been stopped; a second project at `/tmp/fs-health-b/` has been created via `flowstate init`; `flowstate server` is started from `/tmp/fs-health-b/`
  When: `curl -sS http://127.0.0.1:9090/health` is executed
  Then:
  - `project.slug` now starts with `fs-health-b-` (different slug)
  - `project.root` ends in `fs-health-b`
  - `version` is identical to TEST-14 (same install)
  - Confirms that `/health` reads the mounted project from app state, not a hardcoded value

### TEST-16: `/health` does not leak unrelated filesystem paths

  Given: The running server from TEST-14 or TEST-15
  When: The `/health` JSON body is inspected
  Then:
  - The JSON object contains exactly the keys `status`, `version`, `project`
  - `project` contains exactly `slug` and `root`
  - No additional fields expose `$HOME`, `db_path`, `workspaces_dir`, or other internal paths (the response is explicitly minimal)

### TEST-17: Full user journey end-to-end (the demo)

This is the single canonical E2E path the evaluator runs last to prove all four issues compose correctly. Every step must pass; any failure fails the sprint.

  Given: A fresh scratch directory `/tmp/fs-journey/` containing **only** `package.json` (`{"name":"journey-demo"}`), and no running Flowstate server
  When: The following sequence is executed from `/tmp/fs-journey/`:
    1. `flowstate server` (before init) — expected to exit 2 within 5s with the friendly error
    2. `flowstate init` — expected to exit 0
    3. `flowstate check flows/example.flow` — expected to exit 0
    4. `flowstate server` launched as a background process — expected to start on `127.0.0.1:9090` with no WARNING banner
    5. Poll `GET http://127.0.0.1:9090/health` for up to 10 seconds until it returns 200
    6. Parse the `/health` JSON
    7. Stop the server (SIGTERM)
  Then:
  - Step 1 exits with code 2 and stderr points at `flowstate init`
  - Step 2 creates `flowstate.toml` and `flows/example.flow`, and the flow file contains a Node marker (grep for `npm` or `node`) because `package.json` was present
  - Step 3 exits 0 (the seeded Node template is type-safe)
  - Step 4's stderr contains no `WARNING` line
  - Step 5's first successful response body satisfies:
    - `status == "ok"`
    - `project.slug` starts with `fs-journey-`
    - `project.root` is `/tmp/fs-journey` (resolved absolute)
    - `version` is a non-empty string
  - Step 7 shuts down cleanly (exit within 5s of SIGTERM)
  - No file named `flowstate.toml` or `example.flow` was created anywhere **outside** `/tmp/fs-journey/` (no pollution of the dev repo or `$HOME`)
  - No new file was created directly under `~/.flowstate/` at the legacy paths (`~/.flowstate/flowstate.db`, `~/.flowstate/workspaces/`) — all new state lives under `~/.flowstate/projects/fs-journey-*/`

## Out of Scope

- **PyPI publishing / installer story** — SHARED-010, Phase 31.3.
- **UI bundled in the wheel / served from `importlib.resources`** — SHARED-008 / SERVER-032, Phase 31.3. `flowstate server` may still return 404 at `/` in this sprint; evaluator only probes `/health` and the CLI, not the UI shell.
- **Lumon optional extra** — SHARED-009, Phase 31.3.
- **Authentication on `/health` or any other endpoint** — v0.1 is explicitly no-auth (see §13.4). The host warning is the only mitigation.
- **Env-var overrides** for host/port (e.g. `FLOWSTATE_HOST`) — not in this sprint.
- **Rich multi-file project scaffolding** (`.gitignore`, README, etc.) — `init` writes exactly `flowstate.toml` and `flows/example.flow`, nothing else.
- **Migration prompts** for users who already have a legacy `~/.flowstate/flowstate.db` — greenfield, untouched (carried over from 31.1).
- **Integration of `/health` with any external monitoring** — endpoint exists; nothing consumes it yet.
- **`flowstate init` discovering monorepo sub-packages** — detection looks only at CWD, not descendants or ancestors.
- **Hot reload of `flowstate.toml`** — the running server still pins its project at startup.

## Integration Points

All four issues live in the server domain and share two files: `src/flowstate/cli.py` and `src/flowstate/server/app.py`. There is no cross-domain contract to negotiate, but there are intra-domain contracts the implementing agent must respect:

- **`_require_project()` helper (SERVER-029)** is the **single** entry point every project-requiring command uses. SERVER-028's `init` command is the only command that must **not** call it. SERVER-030's `server` command **must** call it before calling `_warn_if_non_loopback()`, so "no project" fails before "non-loopback" is even checked.
- **Host resolution order (SERVER-030)**: CLI `--host` flag > `flowstate.toml` `[server].host` > default `127.0.0.1`. The warning check runs on the **resolved** host, after precedence is applied, exactly once, from `cli.py::server` (not from `app.py`).
- **`/health` project source (SERVER-031)**: the handler reads the `Project` that `create_app(project)` mounts on `app.state` (the same wiring Phase 31.1 established). It does **not** call `resolve_project()` at request time — the running server is pinned to one project.
- **`init_templates/` package data (SERVER-028)**: the four `.flow` templates and the `flowstate.toml.tmpl` ship as package data under `src/flowstate/init_templates/`. They are loaded via `importlib.resources.files("flowstate.init_templates")`, so they must work from both a source checkout (`uv run flowstate init`) and an installed wheel. The agent must update `pyproject.toml` packaging config if necessary to include the templates — but **must not** add new top-level packages.
- **Version source (SERVER-031)**: `importlib.metadata.version("flowstate")` with a `"0.0.0+dev"` fallback on `PackageNotFoundError`. The fallback exists precisely so source checkouts (including this worktree) answer `/health` without raising.

## Sequencing Within the Sprint

The four issues all touch `cli.py` and `app.py`. Running them in parallel would guarantee merge conflicts and broken intermediate states. **The orchestrator must dispatch all four issues to a single server-dev agent, to be implemented in sequence.**

Recommended order (dependencies + narrative flow):

1. **SERVER-028** first — `flowstate init`. Every downstream test needs a way to create a project from scratch without hand-writing `flowstate.toml`, and TEST-17's journey opens with `init`. Lands the `init_templates/` package and wires the new `init` Typer command.
2. **SERVER-029** second — pretty `ProjectNotFoundError`. Factors out `_require_project()` and migrates every existing project-requiring command. The `init` command from step 1 is the **only** exception and must be explicitly excluded.
3. **SERVER-030** third — host warning. Adds `_warn_if_non_loopback()` in `cli.py::server`, called after `_require_project()` succeeds and the resolved host is known. Default remains `127.0.0.1`.
4. **SERVER-031** last — `/health` endpoint. Adds the router to `app.py`, wires the version + project-from-app-state dependency, and becomes the readiness probe for the evaluator's E2E harness in TEST-11, TEST-12, TEST-13, TEST-14, TEST-15, TEST-16, and TEST-17.

Between each step, the agent must run `/test` and `/lint` locally so regressions surface at the boundary where they were introduced, not after a four-issue stack.

## Done Criteria

This sprint is complete when:

- All 17 acceptance tests above pass against a **freshly restarted** server (no stale `flowstate server` processes from Phase 31.1 or earlier).
- Every per-issue acceptance checklist is checked off in the four issue files.
- Every issue's **E2E Verification Log** section is filled in with the exact commands run and observed output, including at least the sequence from TEST-17 captured verbatim.
- `/test` passes with no regressions — Phase 31.1 test count + the new unit tests for init, CLI errors, host warning, and `/health`.
- `/lint` passes (ruff + pyright clean) in `src/flowstate/`.
- The evaluator verdict is PASS against this sprint contract (saved to `issues/evals/sprint-phase-31-2-eval.md`).
- `issues/PLAN.md` is updated: SERVER-028, SERVER-029, SERVER-030, SERVER-031 → `done`.
- No new files appear under `/tmp/fs-journey/`, `/tmp/fs-init-*/`, or `/tmp/fs-polish-*/` are left behind in the worktree's git status (the tests use absolute paths outside the repo; they must not leak into `issues/`, `src/`, or the evaluator's cwd).

## Risks & Concerns

- **Package-data packaging**: `init_templates/` shipping in the wheel is a common pitfall with hatchling. The agent must verify that `importlib.resources.files("flowstate.init_templates")` resolves in a **source checkout** (`uv run flowstate init`) during the E2E run — not just in a built wheel. If packaging requires a `pyproject.toml` edit, the change is in-scope for SERVER-028.
- **Test isolation from Phase 31.1**: the previous sprint's fixtures live under `/tmp/fs-sprint-a/` and `/tmp/fs-sprint-b/`. This sprint deliberately uses disjoint prefixes (`fs-init-*`, `fs-polish-*`, `fs-health-*`, `fs-journey`, `fs-no-project`) so TEST-15's "slug change" assertion doesn't accidentally match a leftover slug. Evaluator should `rm -rf` these prefixes before running the batch.
- **Exit code 2 vs. 1**: Typer's default for `typer.Exit(code=2)` collides with Click's "usage error" code. This is intentional per SERVER-029, but any existing test that asserts `result.exit_code == 1` for "missing config" must be updated in the same commit.
- **Background server lifecycle in TEST-17**: The evaluator must SIGTERM (not SIGKILL) the server at step 7 so cleanup hooks run. The port used (9090) should be confirmed free at the start of the journey test.
- **Host warning noise in tests**: TEST-12 launches a real server on `0.0.0.0:9091`. On shared machines, this briefly exposes the port. The evaluator should only run TEST-12 on developer machines, not shared CI runners where 0.0.0.0 binds could be policy-blocked. If CI fails only TEST-12, the agent should not attempt to "fix" it by weakening the warning — escalate instead.
- **`/health` version mismatch**: this worktree is a source checkout, so `importlib.metadata.version("flowstate")` may raise `PackageNotFoundError` depending on whether `flowstate` has been `pip install -e .`d into the active venv. TEST-14 accepts both a PEP 440 version **and** the `"0.0.0+dev"` fallback to cover both cases.
- **`flowstate check` against seeded templates**: all four starter flows must be type-safe (per SERVER-028 acceptance). If any template fails `check` (e.g., Rust template references a missing harness), TEST-1 through TEST-4 all fail. The agent should run `flowstate check` against every generated template during implementation, not just at E2E time.
- **Grep markers for template detection**: TEST-2/3/4 grep for loose substrings (`npm`, `pytest`, `cargo`). If the templates happen to share vocabulary (e.g., a Python template mentioning `cargo cult` in a comment), the assertions would false-positive. The agent should keep marker tokens distinct and domain-specific.
