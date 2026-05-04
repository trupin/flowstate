# Evaluation: Sprint Phase 31.2 — Bootstrap UX (Round 2)

**Date**: 2026-04-11
**Sprint**: sprint-phase-31-2
**Issues**: SERVER-028, SERVER-029, SERVER-030, SERVER-031
**Verdict**: PASS

## Round 2 vs Round 1

Round 1 returned FAIL (14/17 PASS). Three tests changed outcomes in Round 2:

| Test | Round 1 | Round 2 | Change rationale |
|------|---------|---------|------------------|
| TEST-8 | FAIL | **PASS** | Sprint contract updated: `flowstate check` is now explicitly exempt from the project-requirement list (it is a pure DSL validator per specs.md §13.2). Evaluator no longer tests `check`; only `run`, `runs`, `status` are verified outside a project — and all three exit 2 with the friendly message, unchanged from Round 1. |
| TEST-10 | FAIL | **PASS** | `flowstate --version` and `-V` now exist as an eager Typer top-level callback. Both exit 0 and print `flowstate 0.1.0`. `--help` still works and lists `init, check, server, run` among its commands. None trigger "No flowstate.toml found". |
| TEST-11 | FAIL (strict) | **PASS** | Sprint contract + implementation both updated. TEST-11 now probes for the SERVER-030 banner signature specifically (`Flowstate is binding to` substring and `============================================================` border line). Additionally, the pre-existing `WARNING flowstate.server.app: UI dist directory not found` log has been downgraded to `INFO` and reworded (`UI bundle not found … serving API only`). Stderr on default-bind contains neither banner string. |
| TEST-17 step 4 | FAIL (strict) | **PASS** | Same root cause as TEST-11 — the step-4 stderr check now uses banner-specific matching, and the UI-bundle log is INFO. The full 7-step journey now composes cleanly end-to-end. |

All 14 tests that passed in Round 1 continue to pass in Round 2 with no regressions.

## Headline

**17 of 17 tests PASS.** The fresh-user journey (`init → check → server → /health`) works top-to-bottom with zero hand-holding. The host warning banner fires only on non-loopback binds and is silent on the default loopback bind. `/health` is minimal, project-aware, and tracks the mounted project across server restarts. All four scaffolded templates (generic, Node, Python, Rust) parse and type-check. No pollution of `~/.flowstate/` — all scratch state went to `FLOWSTATE_DATA_DIR=/tmp/fs-eval-312r2-data`. The user's dev server on port 9090 (PID 93684) was not touched.

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | Not re-audited per-file in Round 2; Round 1 already confirmed all four issues have filled Post-Implementation Verification sections. |
| Commands are specific and concrete | PASS | Every test recorded exact commands, PIDs, exit codes, and stdout/stderr excerpts. |
| Real E2E (no mocks/TestClient) | PASS | Uses `uv --project <worktree> run flowstate ...` against real scratch dirs under `/tmp`, real `curl` for `/health`, real `nohup &` servers on ports 9093–9097. No TestClient. |
| Scenarios cover acceptance criteria | PASS | Every one of the 17 criteria was exercised directly in this evaluation run. |
| Server restarted after changes | PASS | Every scratch server launched as a fresh background process with a new PID; no stale processes from Round 1 were reused. |
| Reproduction logged before fix (bugs) | N/A | Feature sprint + fix-loop on a feature sprint; no bug reproductions required. |

## Criteria Results

| #  | Criterion (abbrev) | Result | Notes |
|----|--------------------|--------|-------|
| 1  | `init` in empty dir (generic) | PASS | `flowstate.toml` has `host="127.0.0.1"`, `port=9090`; `flows/example.flow` created; Next-block printed; `check` exits 0. |
| 2  | Node detection | PASS | Template contains `// This flow walks a Node codebase through install, build, and test using npm.` plus `npm install`, `npm run build`, `npm test`. Diff from generic non-empty. `check` passes. |
| 3  | Python detection | PASS | Template contains `uv run pytest`, `ruff check`, `pip install -e .`. `check` passes. |
| 4  | Rust detection | PASS | Template contains `cargo build`, `cargo test`, `rustc`. `check` passes. |
| 5  | Refuse clobber w/o `--force` | PASS | Exit 1. Stderr mentions `flowstate.toml already exists` and `--force`. Mtime and marker line preserved (1775957526 → 1775957526). |
| 6  | `--force` rewrites toml, preserves flow | PASS | Exit 0. Toml mtime advanced (1775957526 → 1775957535). Flow mtime unchanged (1775957534). Flow marker comment `// user's hand-edited flow` still present. Stdout contains `Note: /private/tmp/fs-init-force/flows/example.flow already exists; not overwriting.` |
| 7  | `server` outside project → exit 2 | PASS | Exit 2. Stderr: `No flowstate.toml found in /private/tmp/fs-no-project or any parent directory. Run \`flowstate init\` to create one...`. No traceback. |
| 8  | Other project-requiring commands → exit 2 | **PASS** | `run`, `runs`, `status` all exit 2 with friendly message, no traceback. `check` is exempt per updated contract (§13.2: pure DSL validator, operates without project context). |
| 9  | `init` works outside any project | PASS | Exit 0. `flowstate.toml` and `flows/example.flow` created at `/tmp/fs-no-project2`. |
| 10 | `--version` / `--help` outside project | **PASS** | `flowstate --version` → `flowstate 0.1.0` (exit 0). `flowstate -V` → `flowstate 0.1.0` (exit 0). `flowstate --help` lists `init, check, server, run, runs, status, schedules, trigger`. No "No flowstate.toml found" on any. |
| 11 | Default server does NOT print host-warning banner | **PASS** | Stderr contains 0 occurrences of `Flowstate is binding to` and 0 occurrences of the `============================================================` border. The only non-INFO noise is gone — the former `WARNING flowstate.server.app: UI dist directory not found` is now `INFO flowstate.server.app: UI bundle not found ... serving API only`. `/health` returns 200. |
| 12 | `--host 0.0.0.0` prints warning banner | PASS | Stderr contains 2 border lines, 1 `WARNING: Flowstate is binding to 0.0.0.0:9094`, 1 `NO AUTHENTICATION`, 1 `Only use non-loopback binds in trusted networks`. Banner printed before Uvicorn startup. `/health` reachable via loopback (200). Printed exactly once. |
| 13 | Explicit `--host 127.0.0.1` no warning | PASS | Banner absent. `/health` 200. |
| 14 | `/health` shape and slug | PASS | Body: `{"status":"ok","version":"0.1.0","project":{"slug":"fs-health-a-deef337a","root":"/private/tmp/fs-health-a"}}`. Top-level keys exactly `status, version, project`. Project keys exactly `slug, root`. Slug starts with `fs-health-a-`. Root ends with `fs-health-a` (macOS `/private/tmp` form accepted per the symlink note). |
| 15 | `/health` reflects project switch on restart | PASS | Second project at `/tmp/fs-health-b/` launched on port 9096. Slug changed from `fs-health-a-deef337a` to `fs-health-b-d3f3ae02`. Root ends with `fs-health-b`. Version identical (`0.1.0`). |
| 16 | `/health` does not leak unrelated paths | PASS | No `db_path`, `workspaces_dir`, `$HOME`, or other internal paths present. Covered by TEST-14 shape validation. |
| 17 | Full user journey E2E | **PASS** | Step 1 exits 2 with friendly message pointing at `flowstate init`. Step 2 creates both files, flow contains `npm`/`node` markers (Node detection from `package.json`). Step 3 exits 0. Step 4 launches on `127.0.0.1:9097`, stderr has 0 banner occurrences. Step 5 `/health` 200 after 1 poll with `slug=fs-journey-ac30af6a, root=/private/tmp/fs-journey, version=0.1.0`. Step 7 SIGTERM stopped server in 1s. No pollution of `~/.flowstate/projects/fs-journey-*` (none exist); project correctly lives at `/tmp/fs-eval-312r2-data/projects/fs-journey-ac30af6a`. |

## Failures

None.

## Evidence — Round 2 Command Transcripts

### Setup

```
$ lsof -iTCP:9090 -sTCP:LISTEN    # → PID 93684 (user's dev server, NOT touched)
$ lsof -iTCP:9093..9099           # → all free
$ rm -rf /tmp/fs-init-* /tmp/fs-polish-* /tmp/fs-health-* /tmp/fs-journey /tmp/fs-no-project*
$ mkdir -p /tmp/fs-eval-312r2-data
$ export FLOWSTATE_DATA_DIR=/tmp/fs-eval-312r2-data
$ WT=/Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability
```

### TEST-8 (PASS — check is exempt)

```
$ cd /tmp/fs-no-project
$ uv --project $WT run flowstate run flows/foo.flow
No flowstate.toml found in /private/tmp/fs-no-project or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2   ← PASS

$ uv --project $WT run flowstate runs
No flowstate.toml found in /private/tmp/fs-no-project or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2   ← PASS

$ uv --project $WT run flowstate status fake-id
No flowstate.toml found in /private/tmp/fs-no-project or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2   ← PASS

(flowstate check is exempt per sprint contract update and specs.md §13.2)
```

### TEST-10 (PASS — --version now exists)

```
$ cd /tmp/fs-no-project
$ uv --project $WT run flowstate --version
flowstate 0.1.0
exit=0   ← PASS

$ uv --project $WT run flowstate -V
flowstate 0.1.0
exit=0   ← PASS

$ uv --project $WT run flowstate --help
 Usage: flowstate [OPTIONS] COMMAND [ARGS]...
 State-machine orchestration system for AI agents.
 Options: --version -V  Show the Flowstate version and exit.
          --help        Show this message and exit.
 Commands: init, check, server, run, runs, status, schedules, trigger
exit=0   ← PASS
```

None triggered "No flowstate.toml found".

### TEST-11 (PASS — no banner, no WARNING-level UI noise)

```
$ mkdir -p /tmp/fs-polish-default && cd /tmp/fs-polish-default
$ uv --project $WT run flowstate init
$ nohup uv --project $WT run flowstate server --port 9093 > server11.log 2>&1 &
pid=1289
$ curl -s http://127.0.0.1:9093/health
{"status":"ok","version":"0.1.0","project":{"slug":"fs-polish-default-65e05c4a","root":"/private/tmp/fs-polish-default"}}
http=200

$ cat server11.log
2026-04-11 21:32:41,941 INFO flowstate.server.app: UI bundle not found at .../ui/dist; serving API only. Run 'cd ui && npm run build' if you want the web UI.
Starting Flowstate server on 127.0.0.1:9093
Project: /private/tmp/fs-polish-default (slug=fs-polish-default-65e05c4a)
INFO:     Started server process [1291]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9093 (Press CTRL+C to quit)

$ grep -c 'Flowstate is binding to' server11.log    # → 0 (banner absent)
$ grep -c '============================================================' server11.log   # → 0 (border absent)
$ kill 1289
```

Note the former `WARNING flowstate.server.app: UI dist directory not found...` from Round 1 is now `INFO flowstate.server.app: UI bundle not found ... serving API only`.

### TEST-12 (PASS — banner printed once, before startup)

```
$ cd /tmp/fs-polish-default
$ nohup uv --project $WT run flowstate server --host 0.0.0.0 --port 9094 > server12.log 2>&1 &
pid=1365
$ cat server12.log
============================================================
WARNING: Flowstate is binding to 0.0.0.0:9094.
Flowstate v0.1 has NO AUTHENTICATION. Anyone who can reach
this address can execute code on this machine via Flowstate's
subprocess harnesses.
Only use non-loopback binds in trusted networks.
============================================================
2026-04-11 21:32:54,962 INFO flowstate.server.app: UI bundle not found ...
Starting Flowstate server on 0.0.0.0:9094
Project: /private/tmp/fs-polish-default (slug=fs-polish-default-65e05c4a)
INFO:     Uvicorn running on http://0.0.0.0:9094 (Press CTRL+C to quit)

$ grep -c '============================================================' server12.log   # → 2
$ grep -c 'WARNING: Flowstate is binding to 0.0.0.0:9094' server12.log                 # → 1
$ grep -c 'NO AUTHENTICATION' server12.log                                              # → 1
$ grep -c 'Only use non-loopback binds in trusted networks' server12.log                # → 1

$ curl -s http://127.0.0.1:9094/health  # → 200, body matches
$ kill 1365
```

### TEST-14 / TEST-15 (PASS — /health shape and slug switch)

```
$ curl -s http://127.0.0.1:9096/health     # (TEST-14 from /tmp/fs-health-a)
{"status":"ok","version":"0.1.0","project":{"slug":"fs-health-a-deef337a","root":"/private/tmp/fs-health-a"}}

$ curl -s http://127.0.0.1:9096/health     # (TEST-15 from /tmp/fs-health-b, fresh server)
{"status":"ok","version":"0.1.0","project":{"slug":"fs-health-b-d3f3ae02","root":"/private/tmp/fs-health-b"}}

Assertions:
- top-level keys == ['project', 'status', 'version']    ✓
- project keys == ['root', 'slug']                       ✓
- a.version == b.version == '0.1.0'                      ✓
- a.project.slug != b.project.slug                       ✓
- slug prefixes match directory names                    ✓
```

### TEST-17 full journey (PASS)

```
$ mkdir -p /tmp/fs-journey && cd /tmp/fs-journey
$ echo '{"name":"journey-demo"}' > package.json

STEP 1 — server before init
$ timeout 10 uv --project $WT run flowstate server
No flowstate.toml found in /private/tmp/fs-journey or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2   ← PASS

STEP 2 — init
$ uv --project $WT run flowstate init
Created flowstate.toml and flows/example.flow.
Next:
  flowstate check flows/example.flow
  flowstate server
exit=0
$ grep -E 'npm|node' flows/example.flow   # → present (Node detection from package.json)

STEP 3 — check
$ uv --project $WT run flowstate check flows/example.flow
OK
exit=0

STEP 4 — server (background)
$ nohup uv --project $WT run flowstate server --port 9097 > journey.log 2>&1 &
pid=1671
$ cat journey.log
2026-04-11 21:33:52,551 INFO flowstate.server.app: UI bundle not found at .../ui/dist; serving API only. Run 'cd ui && npm run build' if you want the web UI.
Starting Flowstate server on 127.0.0.1:9097
Project: /private/tmp/fs-journey (slug=fs-journey-ac30af6a)
INFO:     Uvicorn running on http://127.0.0.1:9097 (Press CTRL+C to quit)

$ grep -c 'Flowstate is binding to' journey.log    # → 0  (banner absent — PASS)
$ grep -c '============================================================' journey.log   # → 0  (border absent — PASS)

STEP 5 — poll /health
$ curl -s http://127.0.0.1:9097/health    # → 200 on first poll
{"status":"ok","version":"0.1.0","project":{"slug":"fs-journey-ac30af6a","root":"/private/tmp/fs-journey"}}

STEP 6 — parse JSON
  status == "ok"                                           ✓
  project.slug startswith "fs-journey-" (fs-journey-ac30af6a) ✓
  project.root == "/private/tmp/fs-journey" (resolved)     ✓
  version == "0.1.0" (non-empty)                           ✓

STEP 7 — SIGTERM
$ kill 1671
  stopped after 1s                                          ✓

POLLUTION CHECK
  ~/.flowstate/projects/fs-journey-*           → none (correct, scratch data dir used)
  /tmp/fs-eval-312r2-data/projects/fs-journey-ac30af6a → present (correct home)
```

Post-eval port scan: `lsof -iTCP:9093..9097` → all free. `lsof -iTCP:9090` → PID 93684 (user's dev server, untouched).

## Summary

**17 of 17 criteria PASS.** All three Round-1 failures are resolved:

- **TEST-8**: sprint contract correctly exempts `flowstate check` from project-requirement per specs.md §13.2. Evaluator verified `run`, `runs`, `status` all exit 2 with the friendly message outside a project.
- **TEST-10**: `flowstate --version` / `-V` now print `flowstate 0.1.0` (exit 0). `--help` still works.
- **TEST-11 / TEST-17 step 4**: default-bind stderr contains no SERVER-030 banner substring. UI-dist log downgraded from WARNING to INFO and reworded.

No regressions in any previously-passing test. The fresh-user journey composes cleanly end-to-end. The user's dev server on port 9090 was not touched (PID 93684 remained alive throughout; all scratch servers used ports 9093–9097).

**Verdict: PASS.**
