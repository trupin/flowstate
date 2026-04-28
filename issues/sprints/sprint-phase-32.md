# Sprint Phase 32 — Dev/deployed isolation hygiene

**Issues**: ENGINE-081, ENGINE-082, ENGINE-083
**Domains**: engine (all three)
**Date**: 2026-04-25
**Phase**: 32 (follows Phase 31.1/31.2/31.3, all done + evaluator-PASSED)

## Goal

Phase 31 made Flowstate publishable as a project-rooted dev server with bundled UI and a per-project `Project` contract under `~/.flowstate/projects/<slug>/`. Phase 32 closes three small but real holes where the engine still bypasses that contract:

1. The scheduler writes triggered-run `data_dir` strings into the DB under the global `~/.flowstate/runs/` namespace instead of the per-project one. Two projects scheduling the same flow on the same minute can collide on schedule IDs.
2. The executor falls back to a hardcoded `http://127.0.0.1:9090` when its `_server_base_url` is `None`, so a subprocess of a 9091-bound server can silently call back to a different 9090-bound server.
3. Lumon's global plugins resolver still hardcodes `Path.home() / ".flowstate" / "plugins"` and ignores `FLOWSTATE_DATA_DIR`.

End state: two scratch projects on different ports (9091 and 9092), each with one scheduled flow, are fully isolated on disk, in the DB, and in subprocess environments. `FLOWSTATE_DATA_DIR=/tmp/custom` redirects all flowstate data including lumon plugins.

## Execution Plan

```
One engine-dev agent, sequential within the agent (file-touch overlap on queue_manager.py / app.py):
  Wave 1: ENGINE-081 (scheduler.py, queue_manager.py, app.py, tests/engine/test_scheduler.py)
          -- adds Scheduler(project=...) parameter; threads Project from QueueManager._project
  Wave 2: ENGINE-082 (executor.py, app.py, queue_manager.py, tests/engine/test_executor.py)
          -- removes hardcoded 9090 fallback; raises FlowExecutorConfigError; wires loopback URL
  Wave 3: ENGINE-083 (engine/lumon.py, tests/engine/test_lumon.py)
          -- one-line swap to _default_data_dir() / "plugins"
  Wave 4: TEST-ALL (full-journey two-project E2E)
```

A single engine-dev agent is preferred over splitting because both ENGINE-081 and ENGINE-082 touch `queue_manager.py` and `app.py`. ENGINE-083 is independent and can be done first or last; sequencing it last keeps the agent's mental model focused on Project/URL wiring up front.

## State-of-Codebase Notes (verified 2026-04-25)

The implementing agent must know these before opening files:

- `QueueManager` already stores `self._project = project` (verified at `src/flowstate/engine/queue_manager.py:62`). The user-supplied note suggested it stores only `_config` — that was outdated. `_project` exists; only the wiring from `QueueManager` to `Scheduler` is missing.
- `FlowScheduler` is currently NOT instantiated in any production code path — only in `tests/engine/test_scheduler.py` (~24 callsites) and the docstring in `scheduler.py:10`. Production scheduling appears to be handled differently. The agent must:
  - Add the `project: Project` constructor parameter and use it for the two `data_dir` strings (the bug fix is in `scheduler.py:160` and `:188`).
  - Update all 24 test callsites to pass a `Project` (use a `tmp_path`-backed fixture).
  - Update the docstring example to match.
  - If no production callsite exists, document that fact in the issue's E2E Verification Log so the evaluator does not look for live wiring that does not exist. The unit-level isolation test (TEST-81.4) plus a DB-level inspection in TEST-ALL is the proof-of-correctness, not live scheduler boot.
- `executor.py` references `_server_base_url` at four lines: `:245` (assigned in `__init__`), `:2636` (the buggy fallback — line moved by 30 from issue's `:2606` due to recent edits), `:3041` (a None check), `:3045` (forwarded). The agent should grep for the `or "http://127.0.0.1:9090"` literal directly rather than trusting the line number.
- `app.py:219` already constructs `server_base_url=f"http://{config.server_host}:{config.server_port}"` and passes it into the executor factory. ENGINE-082's wiring fix is therefore mostly about: (a) removing the silent fallback and (b) switching the host portion from `config.server_host` (which can be `0.0.0.0`) to literal `127.0.0.1` so subprocesses always loop back correctly.
- `tests/server/` collects 390 tests; baseline is "0 failures expected" (see Pre-existing Failure Allowlist).

---

## Acceptance Tests

All tests below are runnable by the evaluator. Tests prefixed **[UNIT]** are pytest unit tests. Tests prefixed **[E2E]** require a real running server. Tests prefixed **[GREP]** are static checks against the source tree.

---

### ENGINE-081: Scheduler routes triggered-run `data_dir` through `Project`

**TEST-81.1: No `~/.flowstate/runs/` literal remains in src/** [GREP]
  Given: The post-fix worktree
  When: Running `grep -rn "~/.flowstate/runs" src/flowstate/ | grep -v __pycache__`
  Then: Zero matches

**TEST-81.2: `Scheduler.__init__` accepts a `project` parameter** [UNIT]
  Given: The post-fix `flowstate.engine.scheduler` module
  When: Running `python -c "import inspect; from flowstate.engine.scheduler import FlowScheduler; sig = inspect.signature(FlowScheduler.__init__); assert 'project' in sig.parameters, list(sig.parameters)"`
  Then: Exit code 0

**TEST-81.3: Single-project scheduled trigger writes per-project data_dir** [UNIT]
  Given: A `FlowScheduler` constructed with a `Project` rooted at `tmp_path / "projA"` and one schedule due now
  When: The scheduler triggers the schedule (calls `_process_schedule`)
  Then: The DB row for the new flow_run has `data_dir == str(project.data_dir / "runs" / f"scheduled-{schedule_id}")` AND the string starts with `tmp_path / "projA"` AND it contains neither `~/.flowstate/runs/` nor `Path.home() / ".flowstate"`

**TEST-81.4: Two projects produce disjoint scheduled-run paths** [UNIT]
  Given: Two `FlowScheduler` instances built with two distinct `Project`s (`projA` and `projB`, `projA.data_dir != projB.data_dir`), each with one schedule due now
  When: Each scheduler triggers its schedule
  Then: The two resulting DB `data_dir` values are on disjoint path subtrees, both contain `runs/scheduled-`, and neither path is a prefix of the other

**TEST-81.5: `on_overlap=queue` branch also routes through Project** [UNIT]
  Given: A `FlowScheduler` with a `Project` and a schedule whose `on_overlap == "queue"` and an active run exists
  When: The scheduler triggers
  Then: The DB row's `data_dir` equals `str(project.data_dir / "runs" / f"queued-{schedule_id}")` (the parallel branch and the queue branch are both fixed, not just one)

**TEST-81.6: Existing scheduler tests pass after constructor change** [UNIT]
  Given: The 24 existing `FlowScheduler(...)` callsites in `tests/engine/test_scheduler.py`
  When: Running `uv run pytest tests/engine/test_scheduler.py`
  Then: All collected tests pass; pytest summary shows zero failures and zero errors

---

### ENGINE-082: Executor derives `FLOWSTATE_SERVER_URL` from the bound port; no hardcoded fallback

**TEST-82.1: Hardcoded 9090 literal removed from executor.py** [GREP]
  Given: The post-fix worktree
  When: Running `grep -n '127.0.0.1:9090' src/flowstate/engine/executor.py`
  Then: Zero matches

**TEST-82.2: Typed error class exists and is exported** [UNIT]
  Given: The post-fix engine module
  When: Running `python -c "from flowstate.engine.executor import FlowExecutorConfigError; assert issubclass(FlowExecutorConfigError, Exception)"`
  Then: Exit code 0 (the class name may vary; if the agent picks a different name, document it in the issue and update this test). Acceptable alternatives: `FlowExecutorConfigurationError`, `MissingServerBaseURLError`. The evaluator MUST find a typed exception (not a bare `RuntimeError`/`ValueError`) raised on the missing-URL path.

**TEST-82.3: Subprocess env contains the wired URL exactly** [UNIT]
  Given: A `FlowExecutor` (or whichever class owns the env-building method) constructed with `server_base_url="http://127.0.0.1:9091"`
  When: The env-building method that emits `FLOWSTATE_SERVER_URL` is called for a task subprocess
  Then: The resulting env dict contains `FLOWSTATE_SERVER_URL == "http://127.0.0.1:9091"` (NOT `:9090`)

**TEST-82.4: Missing URL raises typed error, not silent fallback** [UNIT]
  Given: A `FlowExecutor` constructed with `server_base_url=None`
  When: The env-building / subprocess-spawn code path runs
  Then: A `FlowExecutorConfigError` (or named alternative per TEST-82.2) is raised with a message containing `"server_base_url"`. The env dict is never produced; nothing returns a default `127.0.0.1:9090`

**TEST-82.5: app.py wires loopback host even when server binds non-loopback** [UNIT or GREP]
  Given: The post-fix `src/flowstate/server/app.py`
  When: Inspecting how `server_base_url` is constructed
  Then: The host portion is the literal string `127.0.0.1` (not `config.server_host`). A grep for `f"http://127.0.0.1:{` in `app.py` returns at least one match; a grep for `f"http://{config.server_host}` returns zero matches in the executor-wiring path

**TEST-82.6: Effective port (CLI override) wins over config** [E2E or UNIT-with-fake-config]
  Given: A project whose `flowstate.toml` declares `server_port = 9090` AND the server is started with `--port 9091` (CLI override)
  When: Inspecting the URL passed to the executor
  Then: The URL contains `:9091`, not `:9090`. (If CLI overrides are not yet plumbed through Project.config in the codebase, the agent must document this in the issue and either route through the same source the warning banner reads, or escalate. A unit test against a fake config object is acceptable proof.)

**TEST-82.7: TestContextModeHandoff deadlock avoided** [UNIT]
  Given: The new tests in `tests/engine/test_executor.py`
  When: Running `uv run pytest tests/engine/test_executor.py -k "not TestContextModeHandoff"`
  Then: All new tests pass without invoking the known-deadlocking `TestContextModeHandoff::test_context_mode_handoff_with_summary` test class. The pytest invocation in the issue's E2E log MUST use a `-k` filter or a separate test class to avoid the deadlock.

---

### ENGINE-083: Lumon global plugins honor `FLOWSTATE_DATA_DIR`

**TEST-83.1: No `Path.home() / ".flowstate"` literal remains in src/** [GREP]
  Given: The post-fix worktree
  When: Running `grep -rn 'Path.home()' src/flowstate/ | grep -v __pycache__ | grep -E '\.flowstate'`
  Then: Zero matches in source files (the only remaining match should be in `src/flowstate/config.py:18` inside `_default_data_dir` itself, which is the intended single source of truth — the grep above excludes that file by design when constructed as `grep -rn 'Path.home()' src/flowstate/engine/ src/flowstate/server/ src/flowstate/dsl/ src/flowstate/state/ | grep -E '\.flowstate'`)

**TEST-83.2: Lumon import is present and uses `_default_data_dir`** [GREP]
  Given: The post-fix `src/flowstate/engine/lumon.py`
  When: Running `grep -n '_default_data_dir' src/flowstate/engine/lumon.py`
  Then: At least one import line and at least one usage line; usage line constructs `_default_data_dir() / "plugins"` (or equivalent — the result must be a `Path` rooted at the env-aware data dir, not `Path.home()`)

**TEST-83.3: `FLOWSTATE_DATA_DIR` redirects the global plugins lookup** [UNIT]
  Given: `monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "custom"))`, `tmp_path / "custom" / "plugins" / "myplugin" / "marker.txt"` exists, and a separate directory `tmp_path / "fake_home" / ".flowstate" / "plugins" / "wrongplugin" / "marker.txt"` exists
  When: `setup_lumon` runs against a fresh worktree under `tmp_path / "wt"`
  Then: The worktree's `plugins/` symlink/copy contains `myplugin` and does NOT contain `wrongplugin` (proves the env var wins over `Path.home()`)

**TEST-83.4: Unset env var falls back to default home location** [UNIT]
  Given: `monkeypatch.delenv("FLOWSTATE_DATA_DIR", raising=False)` and a `myplugin` directory at the default `_default_data_dir() / "plugins" / "myplugin"`
  When: `setup_lumon` runs
  Then: `myplugin` is picked up (proves the unset path still works — no regression of the default behavior)

**TEST-83.5: All 33 existing `test_lumon.py` tests still pass** [UNIT]
  Given: The post-fix engine
  When: Running `uv run pytest tests/engine/test_lumon.py`
  Then: 33+ tests pass with zero failures and zero errors

---

### Integration: Full-journey isolation E2E

**TEST-ALL: Two scratch projects on different ports remain fully isolated** [E2E]

This is the canonical proof that all three fixes hold together against a real running server. The evaluator runs this from the worktree.

  Given:
  - A clean `~/.flowstate-test-32/` (the test uses `FLOWSTATE_DATA_DIR=/tmp/fs-phase-32-data` to keep the real `~/.flowstate/` untouched)
  - Two scratch project directories at `/tmp/fs-phase-32-A/` and `/tmp/fs-phase-32-B/`, each scaffolded by `flowstate init`
  - Each project contains `flows/scheduled.flow`: a single trivial echo-style flow (one node, no judges) and one schedule with `cron = "* * * * *"` (every minute) and `on_overlap = "skip"`

  When: Running this exact sequence:
```bash
set -euxo pipefail
export FLOWSTATE_DATA_DIR=/tmp/fs-phase-32-data
rm -rf /tmp/fs-phase-32-data /tmp/fs-phase-32-A /tmp/fs-phase-32-B
mkdir -p /tmp/fs-phase-32-A /tmp/fs-phase-32-B
(cd /tmp/fs-phase-32-A && uv run --project $WORKTREE flowstate init && cp $WORKTREE/flows/smoke_retry.flow flows/scheduled.flow)  # use any trivial one-node flow
(cd /tmp/fs-phase-32-B && uv run --project $WORKTREE flowstate init && cp $WORKTREE/flows/smoke_retry.flow flows/scheduled.flow)
# Add a schedule row to each project's DB OR define schedules via .flow / API after server boot
# (the implementing agent must pick the lowest-friction mechanism in the current codebase and document it)

(cd /tmp/fs-phase-32-A && uv run --project $WORKTREE flowstate server --port 9091 &) ; SERVER_A=$!
(cd /tmp/fs-phase-32-B && uv run --project $WORKTREE flowstate server --port 9092 &) ; SERVER_B=$!
sleep 3

# Wait long enough for one scheduler tick on each (cron "* * * * *" plus check_interval)
sleep 70

curl -sf http://127.0.0.1:9091/health | python -c "import sys,json; print(json.load(sys.stdin)['project_slug'])"
curl -sf http://127.0.0.1:9092/health | python -c "import sys,json; print(json.load(sys.stdin)['project_slug'])"

# Inspect on-disk isolation
find /tmp/fs-phase-32-data/projects -type d -name 'runs' | sort
test ! -d /tmp/fs-phase-32-data/runs   # no global runs/ namespace was created

# Inspect DB rows: each project's flow_run.data_dir starts under its own project root
sqlite3 /tmp/fs-phase-32-data/projects/*A*/flowstate.db "SELECT data_dir FROM flow_runs;" | tee /tmp/runs_A.txt
sqlite3 /tmp/fs-phase-32-data/projects/*B*/flowstate.db "SELECT data_dir FROM flow_runs;" | tee /tmp/runs_B.txt
grep -v '/projects/.*A.*/runs/' /tmp/runs_A.txt && exit 1 || true
grep -v '/projects/.*B.*/runs/' /tmp/runs_B.txt && exit 1 || true

# Inspect a live subprocess env (ENGINE-082 proof): trigger a flow on each, capture child env
# Approach: use a flow whose first task runs `env | grep FLOWSTATE_SERVER_URL > /tmp/env-A.txt` (or .B)
# then assert each file contains the matching port
grep -F "FLOWSTATE_SERVER_URL=http://127.0.0.1:9091" /tmp/env-A.txt
grep -F "FLOWSTATE_SERVER_URL=http://127.0.0.1:9092" /tmp/env-B.txt
! grep -F "9090" /tmp/env-A.txt
! grep -F "9090" /tmp/env-B.txt

# Lumon plugins env-var honoring (ENGINE-083 proof, lightweight variant)
mkdir -p /tmp/fs-phase-32-data/plugins/sentinel-plugin
test ! -d $HOME/.flowstate/plugins/sentinel-plugin  # ensure no contamination
# Trigger a flow that calls into setup_lumon (or call setup_lumon directly via `python -c`)
# and assert sentinel-plugin appears in the worktree's plugins/ resolution

kill $SERVER_A $SERVER_B
```

  Then: Every command exits 0. Specifically:
  - `find /tmp/fs-phase-32-data/projects -type d -name 'runs'` lists exactly two directories (one per project) under disjoint slug subtrees
  - `/tmp/fs-phase-32-data/runs` does NOT exist (proves no scheduler bypass)
  - Every `data_dir` in project A's DB starts with `/tmp/fs-phase-32-data/projects/<slugA>/runs/`; same for B with `<slugB>`. The two slugs are different
  - `/tmp/env-A.txt` contains the literal string `FLOWSTATE_SERVER_URL=http://127.0.0.1:9091`; `/tmp/env-B.txt` contains `:9092`. Neither file mentions `:9090`
  - The lumon `sentinel-plugin` is resolved from `/tmp/fs-phase-32-data/plugins/`, not `~/.flowstate/plugins/`

  **Degradation paths**:
  - If the codebase has no per-DB schedules table accessible via `sqlite3` (i.e., the existing schema differs from what the issue assumes), the evaluator may substitute a programmatic check via the Project's repository API, documented in the verdict.
  - If the lumon binary is not installed on the test host (lumon is an optional extra), the lumon assertion degrades to a unit-level proof (`python -c "import os; os.environ['FLOWSTATE_DATA_DIR']='/tmp/x'; from flowstate.engine.lumon import _resolve_global_plugins_dir; print(_resolve_global_plugins_dir())"` or equivalent introspection of the resolver). The evaluator must document the degradation in the verdict.
  - If scheduling a flow via `cron = "* * * * *"` is not yet wired to a live `FlowScheduler` instance in production (per the State-of-Codebase note above), the data_dir assertion degrades to invoking `FlowScheduler._process_schedule` directly in a small Python harness against each project's DB, then re-running the `find`/`sqlite3` assertions. This still proves isolation end-to-end on disk and in the DB.

---

## Out of Scope

Everything below is explicitly NOT part of Phase 32. Surfacing an issue in any of these areas during implementation means: file a follow-up issue, do not widen this sprint.

- Migrating any existing `~/.flowstate/runs/` data from prior installs (greenfield only — no migration path needed).
- Refactoring `FlowScheduler` constructor beyond adding the `project: Project` parameter (no signature redesign, no DI container, no factory pattern).
- Auto-port-shift if 9090 (or any chosen port) is taken — `EADDRINUSE` is acceptable as-is for v0.1; the bind-failure UX is not in scope.
- Editable-install UI resolution order — only affects contributors, not the bug surface this sprint addresses.
- Wiring `FlowScheduler` into a live production code path if it is not currently wired (the bug fix and unit-level proof of correctness is sufficient; production wiring is tracked separately if needed).
- Any change to `FLOWSTATE_SERVER_URL`'s consumers inside subprocesses (the SDK runner, the artifact API client). This sprint only fixes the URL the executor injects; downstream consumers of the env var are unchanged.
- Any change to the lumon binary itself or the lumon CLI contract.
- Changes to `~/.flowstate/projects/<slug>/` layout (frozen by Phase 31.1).
- Changes to `flowstate init`, `flowstate server`, or `flowstate check` CLI behavior.
- Any UI changes (UI domain is untouched in Phase 32).

## Integration Contract Across Issues

All three issues are engine-domain. The shared touch surfaces are:

1. **`src/flowstate/engine/queue_manager.py`**:
   - ENGINE-081 adds `Scheduler(project=self._project, ...)` to the construction site.
   - ENGINE-082 ensures the `FlowExecutor` it constructs (if any) is given the loopback URL.
   - ENGINE-083 does NOT touch this file.
2. **`src/flowstate/server/app.py`**:
   - ENGINE-081 may need to thread `project` into a fixture/factory if `QueueManager` is built here.
   - ENGINE-082 changes the `server_base_url` construction to use literal `127.0.0.1`.
   - ENGINE-083 does NOT touch this file.
3. **`src/flowstate/engine/scheduler.py`** (ENGINE-081 only): two literal-string fixes plus constructor signature.
4. **`src/flowstate/engine/executor.py`** (ENGINE-082 only): one fallback removal, one typed-error class, one None-check tightening.
5. **`src/flowstate/engine/lumon.py`** (ENGINE-083 only): one import + one path-expression swap.
6. **`tests/engine/test_scheduler.py`** (ENGINE-081 only): 24 callsite updates, 1 new isolation test.
7. **`tests/engine/test_executor.py`** (ENGINE-082 only): 2 new tests, must use `-k` filter to dodge `TestContextModeHandoff` deadlock.
8. **`tests/engine/test_lumon.py`** (ENGINE-083 only): 1 new env-var test.

Artifacts flowing between issues: none — each issue is self-contained. The TEST-ALL E2E exercises all three fixes simultaneously but does not introduce coupling between them at the implementation level.

---

## Pre-existing Failure Allowlist

The post-implementation test baseline must be:

- **`tests/server/`**: 379 passed (out of 390 collected). The 11-test gap is pre-existing skips/xfails carried over from the post-Phase 31.3 fix-up. No new failures introduced; no tests that previously passed may fail.
- **`tests/engine/test_scheduler.py`**: All currently-passing tests continue to pass with the new `project=` parameter (24 callsites updated). The new `TEST-81.4` two-project isolation test is added.
- **`tests/engine/test_executor.py`**: The existing `TestContextModeHandoff::test_context_mode_handoff_with_summary` deadlock is a known pre-existing issue and is excluded from this sprint's pass criteria via `-k "not TestContextModeHandoff"`. The two new ENGINE-082 tests must pass.
- **`tests/engine/test_lumon.py`**: 33 existing tests + 1-2 new tests (TEST-83.3, TEST-83.4) all pass. If the lumon optional extra is not installed, the existing skip pattern from Phase 31.3's `tests/engine/test_lumon_optional.py` applies and is acceptable.
- **All other test directories**: Unchanged baseline. The full `uv run pytest` invocation must show zero new failures versus the pre-sprint baseline.

If the evaluator observes a failure outside this allowlist, the verdict is FAIL even if the new ENGINE-08x tests pass.

---

## Risks Called Out

1. **`FlowScheduler` has no live production instantiation.** Verified: zero `FlowScheduler(...)` callsites outside tests and one docstring. The fix is still correct and unit-testable, but the agent must NOT spend cycles trying to find a phantom production wiring. Document the absence in the issue's E2E log so the evaluator does not look for it. The TEST-ALL E2E has a documented degradation path that calls `_process_schedule` directly in a small harness.

2. **Constructor change breaks 24 test callsites.** The agent must update every `FlowScheduler(db=db, emit=callback)` call to pass a `Project`. Use a shared `make_test_project(tmp_path)` fixture to keep churn small. Pyright must pass after the update.

3. **Loopback host substitution may break a test that asserts on `0.0.0.0`.** If any test in `tests/server/` asserts that `server_base_url` contains `config.server_host`, that test must be updated to expect `127.0.0.1` (with a comment explaining the loopback-only callback semantic). The agent should grep for `server_base_url` in `tests/` before submitting.

4. **Effective-port plumbing for `--port` CLI override.** If the codebase currently passes `config.server_port` rather than the CLI-overridden bound port, the agent must trace where the override lands (likely uvicorn's runtime port) and source the URL from the same place. If this is non-trivial, document it as a follow-up issue and wire up the simpler `config.server_port` path in this sprint with a TODO comment — TEST-82.6 then degrades to a unit test against a fake config rather than an E2E port-override test. Escalate to the orchestrator if the choice is unclear.

5. **Lumon `_default_data_dir` import surface.** `_default_data_dir` is an underscore-prefixed helper in `flowstate.config`. Importing it from `engine/lumon.py` is intentional per the issue's Technical Design but technically violates the underscore convention. The agent should NOT export it from `lumon.py` or alias it; the import is local and explicit. If pyright complains about the underscored import, suppress with a `# noqa` that cites the issue ID, NOT by renaming `_default_data_dir`.

6. **TEST-ALL `sqlite3` CLI dependency.** macOS and most Linux dev images ship `sqlite3`. If the evaluator's environment lacks it, substitute a `python -c "import sqlite3; ..."` invocation. The disk-path assertions via `find`/`test -d` are unaffected.

7. **Cron `* * * * *` triggers between 0 and 60s after server boot.** The TEST-ALL `sleep 70` is the safety margin. If the scheduler's `check_interval` is longer (e.g., 60s), the sleep may need bumping. The agent should confirm `check_interval` in `scheduler.py` and adjust the sleep if needed; document the value in the E2E log.

---

## Done Criteria

This sprint is complete when ALL of the following are true:

1. All three issues (ENGINE-081, ENGINE-082, ENGINE-083) are marked `done` in their issue files and in `issues/PLAN.md`.
2. Every `[GREP]` test passes (no `~/.flowstate/runs/` literal in src/, no `127.0.0.1:9090` literal in `executor.py`, no `Path.home() / ".flowstate"` literal in engine/server/dsl/state src/).
3. Every `[UNIT]` test passes:
   - TEST-81.2 through TEST-81.6 (scheduler parameter + 4 isolation tests + existing-test green)
   - TEST-82.2 through TEST-82.5 and TEST-82.7 (typed error, env wiring, missing-URL guard, loopback host, deadlock-avoidance)
   - TEST-83.2 through TEST-83.5 (import + 2 new env-var tests + existing-33 green)
4. TEST-ALL E2E passes end-to-end against two real running servers, OR each substituted assertion uses a documented degradation path from the list above.
5. `uv run pytest` passes with no regressions vs the pre-sprint baseline (see Pre-existing Failure Allowlist).
6. `uv run ruff check .` and `uv run pyright` pass with no new errors.
7. `cd ui && npm run lint` is unaffected (UI not touched, but the agent should confirm zero diff in `ui/`).
8. The orchestrator has committed each issue in a separate commit following the `[ENGINE-08x] ...` convention.

The TEST-ALL integration test is the blocking gate: if TEST-ALL fails or its degradations cannot be justified, the sprint does not ship regardless of unit-test status.
