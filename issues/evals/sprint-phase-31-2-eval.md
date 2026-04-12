# Evaluation: Sprint Phase 31.2 — Bootstrap UX

**Date**: 2026-04-11
**Sprint**: sprint-phase-31-2
**Issues**: SERVER-028, SERVER-029, SERVER-030, SERVER-031
**Verdict**: FAIL

## Headline

14 of 17 tests PASS. Three failures, in order of severity:

1. **TEST-10 FAIL (unambiguous)** — `flowstate --version` raises `No such option: --version` (exit 2). The CLI has never defined a version flag. TEST-10 is explicit that `flowstate --version` must exit 0 and print a version string. `flowstate --help` on the other hand works correctly.
2. **TEST-8 FAIL (unambiguous)** — `flowstate check flows/foo.flow` run outside a project exits **1** with `Error: File not found: flows/foo.flow` instead of exit **2** with the "No flowstate.toml found" friendly error. The implementer deliberately exempted `check` from `_require_project()` (self-documented in their E2E log: "`init`, `--help`, and `check` work outside any project"), but the sprint contract TEST-8 lists `check` as a project-requiring command that must fail with code 2 and the friendly message. Either the sprint contract is wrong or the implementation is wrong — per eval protocol, the contract is authoritative.
3. **TEST-11 / TEST-17 step 4 FAIL (strict reading)** — the sprint contract requires that `flowstate server` (default bind) emit no `WARNING` line and no `=====` border line in stderr. A `=====` border is absent (the host-warning-banner signature), but a pre-existing `WARNING flowstate.server.app: UI dist directory not found at .../ui/dist. Static file serving is disabled.` line is emitted on every startup. This is pre-existing noise from Phase 31.1 (or earlier) and is not introduced by Phase 31.2, but the sprint contract was written assuming stderr would be silent on default-bind startup, and the literal text of TEST-11 says "Stderr during startup does **not** contain the word `WARNING` or the `=====` border line". Per "no benefit of the doubt" this is a FAIL. The **intent** of the test (host warning banner absent on loopback bind) is satisfied.

TEST-12, TEST-13, TEST-14, TEST-15, TEST-16 (the core `/health` + host warning mechanics) pass cleanly. The four scaffolded templates all parse and type-check. The journey TEST-17 otherwise works end-to-end with no pollution of the real `~/.flowstate/` tree.

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | All four issue files have a filled-in Post-Implementation Verification section. |
| Commands are specific and concrete | PASS | Full transcript of the journey script with exit codes and stdout/stderr excerpts. |
| Real E2E (no mocks/TestClient) | PASS | Uses `uv run flowstate ...` against scratch dirs under `/tmp`, real `curl` for `/health`, real `nohup &` servers. No TestClient in the proof. |
| Scenarios cover acceptance criteria | PARTIAL | The implementer's E2E script runs a compact 7-step journey that covers the happy path of all four issues, but it does **not** exercise TEST-8 (checking that `flowstate check flows/foo.flow` exits with 2) or TEST-10 (`flowstate --version`). These gaps correspond exactly to the two unambiguous failures I found. |
| Server restarted after changes | PASS | Each log shows a fresh `pid=<N>` and a fresh port (9098, etc.). |
| Reproduction logged before fix (bugs) | N/A | Feature work, not bug fixes. |

The implementer's logs show `project.slug=fs-phase312-proj-687706de` and `project.root=/private/tmp/fs-phase312-proj` — credible (real hash suffix, real resolved path). Nothing suggests fabrication.

## Criteria Results

| #  | Criterion (abbrev) | Result | Notes |
|----|--------------------|--------|-------|
| 1  | `init` in empty dir (generic) | PASS | toml has `host=127.0.0.1` and `port=9090`, flows/example.flow created, "Next:" block present, `check` exits 0. |
| 2  | Node detection | PASS | Template contains `npm install`, `npm run build`, `npm test` — diff from generic non-empty. `check` passes. |
| 3  | Python detection | PASS | Template contains `uv run pytest`, `ruff check`, `pip install -e .`. `check` passes. |
| 4  | Rust detection | PASS | Template contains `cargo build`, `cargo test`, `rustc`. `check` passes. |
| 5  | Refuse clobber w/o `--force` | PASS | Exit 1, stderr contains "flowstate.toml already exists" and "--force", mtime preserved, marker line intact. |
| 6  | `--force` rewrites toml, preserves flow | PASS | toml mtime advanced, flow mtime identical, flow marker comment intact, stdout has `Note: .../flows/example.flow already exists; not overwriting`. |
| 7  | `server` outside project → exit 2 | PASS | Exit 2, friendly message, no traceback. |
| 8  | Other project-requiring commands → exit 2 | **FAIL** | `run`, `runs`, `status` all exit 2 with the friendly message as expected. But `flowstate check flows/foo.flow` exits **1** with `Error: File not found: flows/foo.flow`. Implementer exempted `check` from `_require_project()`; the sprint contract requires exit 2 + the project-not-found message. |
| 9  | `init` works outside any project | PASS | Exit 0, toml + flow created. |
| 10 | `--version` / `--help` outside project | **FAIL** | `--help` works. `--version` fails with `No such option: --version` (exit 2). There is no version flag in the Typer app. |
| 11 | Default server does NOT print host warning | **FAIL (strict)** | `=====` banner absent, "Flowstate is binding" absent — **intent satisfied**. But a pre-existing `WARNING flowstate.server.app: UI dist directory not found...` line is still in stderr, violating the literal text "Stderr during startup does not contain the word WARNING". This is Phase 31.1 noise, not Phase 31.2 regression. |
| 12 | `--host 0.0.0.0` prints warning banner | PASS | Banner present, `=====` border, `WARNING: Flowstate is binding to 0.0.0.0:9094`, `NO AUTHENTICATION`, `Only use non-loopback binds in trusted networks`, printed before server start. Exactly once. `/health` reachable via loopback. |
| 13 | Explicit `--host 127.0.0.1` no warning | PASS | Banner absent. `/health` returns 200. |
| 14 | `/health` shape and slug | PASS | `{"status":"ok","version":"0.1.0","project":{"slug":"fs-health-a-deef337a","root":"/private/tmp/fs-health-a"}}`. Top-level keys exactly `status,version,project`. Project keys exactly `slug,root`. |
| 15 | `/health` reflects project switch on restart | PASS | Restarted from `/tmp/fs-health-b/`; slug/root change correctly, version stays the same. |
| 16 | `/health` does not leak unrelated paths | PASS | No `db_path`, `workspaces_dir`, `$HOME`, etc. in response. |
| 17 | Full user journey end-to-end | **FAIL (strict)** | Steps 1–7 all functionally succeed; **step 4 strict reading FAILs** for the same "stderr contains no WARNING line" reason as TEST-11. Pollution check clean: no new files in `~/.flowstate/projects/fs-journey-*` (scratch server was run with `FLOWSTATE_DATA_DIR=/tmp/fs-eval-312-data`; project lives at `/tmp/fs-eval-312-data/projects/fs-journey-ac30af6a`). |

## Failures

### FAIL-1: `flowstate --version` is not defined
**Criterion**: TEST-10 — "`flowstate --version` and `flowstate --help` each exit 0; `--version` prints a version string".
**Expected**: `flowstate --version` exits 0 and prints some non-empty version string.
**Observed**:
```
$ uv --project <worktree> run flowstate --version
Usage: flowstate [OPTIONS] COMMAND [ARGS]...
Try 'flowstate --help' for help.
╭─ Error ──────────────────────────────────────────────────────────────────────╮
│ No such option: --version                                                    │
╰──────────────────────────────────────────────────────────────────────────────╯
exit=2
```
Also tried `flowstate version`, `flowstate -v`, `flowstate -V` — all fail. No version entry point exists at all. This is odd given that SERVER-031 pulls a version from `importlib.metadata.version("flowstate")` for `/health` — that same source should be wired into a top-level `--version` callback.

**Steps to reproduce**:
1. `cd /tmp/fs-no-project` (any dir).
2. `uv --project <worktree> run flowstate --version`
3. Observe exit 2 with "No such option: --version".

### FAIL-2: `flowstate check` outside a project exits 1, not 2
**Criterion**: TEST-8 — "Each of the following is run in turn: `flowstate run flows/foo.flow`, `flowstate check flows/foo.flow`, `flowstate runs`, `flowstate status <fake-id>` ... Every invocation exits with code 2. Every stderr contains the same `No flowstate.toml found` message."
**Expected**: `flowstate check flows/foo.flow` in `/tmp/fs-no-project/` (no ancestor toml) exits 2 with the friendly project-not-found message.
**Observed**:
```
$ cd /tmp/fs-no-project
$ uv --project <worktree> run flowstate check flows/foo.flow
Error: File not found: flows/foo.flow
exit=1
```
The other three commands (`run`, `runs`, `status`) all exit 2 with the correct friendly message. Only `check` is broken. The implementer's log in `issues/server/029-*` explicitly states: "`init`, `--help`, and `check` work outside any project" — so this is intentional, but it contradicts the sprint contract. If the contract is wrong and `check` should be exempt, the sprint-planner must update TEST-8; otherwise the implementation must route `check` through `_require_project()`.

**Steps to reproduce**:
1. `mkdir -p /tmp/fs-no-project && cd /tmp/fs-no-project`
2. `uv --project <worktree> run flowstate check flows/foo.flow`
3. Observe exit 1 with "File not found" instead of exit 2 with "No flowstate.toml found".

### FAIL-3: Default `flowstate server` stderr contains a `WARNING` line (pre-existing noise, strict-read fail)
**Criteria**: TEST-11 and TEST-17 step 4 — "Stderr during startup does **not** contain the word `WARNING` or the `=====` border line".
**Expected**: No `WARNING` substring in stderr on default-bind startup.
**Observed**:
```
$ uv --project <worktree> run flowstate server --port 9093
2026-04-11 21:17:13,228 WARNING flowstate.server.app: UI dist directory not found at .../ui/dist. Static file serving is disabled. Run 'cd ui && npm run build' to build the UI.
Starting Flowstate server on 127.0.0.1:9093
Project: /private/tmp/fs-polish-default (slug=fs-polish-default-65e05c4a)
INFO:     Uvicorn running on http://127.0.0.1:9093 (Press CTRL+C to quit)
```
The `=====` border and "Flowstate is binding to" strings — the **host warning banner's** signature — are absent. The test's intent (host banner is suppressed on loopback) is satisfied. But the literal criterion text forbids **any** `WARNING` token, and the pre-existing UI-dist log emits one. This is not a regression caused by Phase 31.2; the UI static-files code has been emitting this warning since before this sprint. Nevertheless, per "no benefit of the doubt", this fails.

**Steps to reproduce**:
1. `cd /tmp/fs-polish-default` (any init'd project)
2. `uv --project <worktree> run flowstate server --port 9093`
3. Observe stderr contains `WARNING flowstate.server.app: UI dist directory not found`.

**Remediation options** (one, not both):
- Sprint contract tightens the assertion to "Stderr contains no `=====` border line and no `Flowstate is binding to` line". Pragmatic, matches intent.
- Phase 31.2 silences (or demotes to INFO) the UI-dist-missing log when running from a source checkout. Nice-to-have but out of the sprint's stated scope.

## Integration Issues Not Captured by a Specific Test

None — the three failures above are the only observable discrepancies. The positive evidence is strong:

- All four scaffolded templates (`generic`, `node`, `python`, `rust`) are parseable and type-safe (`flowstate check` returns `OK`).
- `/health` correctly reads from `app.state` (TEST-15 proves the slug changes across restarts — no hard-coding).
- `/health` payload is minimal (only `status`, `version`, `project.slug`, `project.root` — no path leakage).
- The host warning banner is idempotent (printed exactly once) and appears before server startup.
- Scratch server in TEST-17 was run with `FLOWSTATE_DATA_DIR=/tmp/fs-eval-312-data`, so no pollution of the real `~/.flowstate/` tree.
- The worktree's pre-existing `flowstate.toml` at repo root does not interfere with scratch tests under `/tmp` (ancestor walk-up from `/private/tmp/...` does not reach the worktree).

## Suggested Follow-ups

1. **FAIL-1**: Add a `--version` top-level callback to `cli.py`. Reuse the same `importlib.metadata.version("flowstate")` source that `/health` uses, with the same `"0.0.0+dev"` fallback.
2. **FAIL-2**: Decide between:
   - (a) Route `check` through `_require_project()`. Pro: consistent CLI behavior. Con: `check` becomes a "useful only inside a project" tool, which is counter-intuitive since `.flow` files are validated without DB/subprocess access. Also breaks any user who runs `flowstate check /path/to/foo.flow` as a pure syntax checker.
   - (b) Update the sprint contract TEST-8 to remove `check` from the list. This is more consistent with how parsers usually work (no need for a project). The issue SERVER-029 criterion "Every CLI command that requires a project" can be read as implicitly excluding `check`.
   - My recommendation is (b) — update the sprint contract. But the call belongs to the sprint-planner/user, not the evaluator.
3. **FAIL-3**: Fix the UI-dist-missing log level. On a source checkout with no `ui/dist/`, logging at WARNING is noisy; log at INFO or skip the message entirely (the server correctly degrades — the log is informational, not a real problem). Also consider refining TEST-11 to probe for the host-warning signature specifically rather than the literal `WARNING` substring.
4. (Minor) TEST-6 stdout says `Created flowstate.toml and flows/example.flow.` even when the flow is **preserved** (not rewritten). The preceding `Note: ... already exists; not overwriting.` line carries the correct information, but "Created" is technically inaccurate. Consider adjusting the message to `Regenerated flowstate.toml; kept flows/example.flow.` when the flow is preserved.

## Evidence — Full Command Transcripts

### Setup
```
$ cd /Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability
$ lsof -iTCP:9093..9099 -sTCP:LISTEN → all free
$ rm -rf /tmp/fs-init-* /tmp/fs-polish-* /tmp/fs-health-* /tmp/fs-journey /tmp/fs-no-project*
$ mkdir -p /tmp/fs-eval-312-data
$ export FLOWSTATE_DATA_DIR=/tmp/fs-eval-312-data   # all subsequent scratch servers use this
```

### TEST-1 (PASS)
```
$ mkdir -p /tmp/fs-init-empty && cd /tmp/fs-init-empty
$ flowstate init
Created flowstate.toml and flows/example.flow.
Next:
  flowstate check flows/example.flow
  flowstate server
exit=0
$ cat flowstate.toml    # → host = "127.0.0.1", port = 9090
$ flowstate check flows/example.flow
OK
exit=0
```

### TEST-2 (PASS)
```
$ mkdir -p /tmp/fs-init-node && cd /tmp/fs-init-node
$ echo '{"name":"demo"}' > package.json
$ flowstate init
Created flowstate.toml and flows/example.flow.
exit=0
$ grep -E 'npm|node' flows/example.flow
// This flow walks a Node codebase through install, build, and test using npm.
flow example_node { ... npm install ... npm run build ... npm test ... }
$ flowstate check flows/example.flow
OK
$ diff /tmp/fs-init-empty/flows/example.flow /tmp/fs-init-node/flows/example.flow   # non-empty
```

### TEST-3 (PASS)
```
$ mkdir -p /tmp/fs-init-py && cd /tmp/fs-init-py
$ printf '[project]\nname="demo"\nversion="0.1.0"\n' > pyproject.toml
$ flowstate init
exit=0
$ grep -E 'pytest|ruff|pip|python' flows/example.flow
// ... uv run pytest ... ruff check ... pip install -e . ...
$ flowstate check flows/example.flow
OK
```

### TEST-4 (PASS)
```
$ mkdir -p /tmp/fs-init-rust && cd /tmp/fs-init-rust
$ printf '[package]\nname="demo"\nversion="0.1.0"\nedition="2021"\n' > Cargo.toml
$ flowstate init
exit=0
$ grep -E 'cargo|rustc' flows/example.flow
// ... cargo build ... cargo test ... rustc ...
$ flowstate check flows/example.flow
OK
```

### TEST-5 (PASS)
```
$ mkdir -p /tmp/fs-init-force && cd /tmp/fs-init-force
$ printf '# user-edited marker line\n[server]\nhost="127.0.0.1"\nport=9090\n' > flowstate.toml
$ stat -f '%m' flowstate.toml     # → 1775956560
$ flowstate init
flowstate.toml already exists at /private/tmp/fs-init-force/flowstate.toml. Use --force to overwrite.
exit=1
$ stat -f '%m' flowstate.toml     # → 1775956560 (unchanged)
$ grep 'user-edited marker' flowstate.toml    # still present
```

### TEST-6 (PASS)
```
$ cd /tmp/fs-init-force
$ mkdir -p flows
$ printf "// user's hand-edited flow\nflow example_hello {\n budget=10m\n on_error=pause\n context=session\n input{greeting:string=\"hi\"}\n entry greet{prompt=\"hi\"}\n exit done{prompt=\"done\"}\n greet->done\n}\n" > flows/example.flow
$ stat -f '%m' flows/example.flow   # → 1775956571
$ stat -f '%m' flowstate.toml        # → 1775956560
$ sleep 1
$ flowstate init --force
Note: /private/tmp/fs-init-force/flows/example.flow already exists; not overwriting.
Created flowstate.toml and flows/example.flow.
Next:
  flowstate check flows/example.flow
  flowstate server
exit=0
$ stat -f '%m' flows/example.flow    # → 1775956571 (UNCHANGED, correct)
$ stat -f '%m' flowstate.toml         # → 1775956572 (advanced, correct)
$ grep "user's hand-edited flow" flows/example.flow  # still present
$ grep "user-edited marker" flowstate.toml           # gone (correct)
```
Minor: stdout's `Created flowstate.toml and flows/example.flow.` is technically inaccurate when the flow is preserved. Preceding `Note:` line carries correct info.

### TEST-7 (PASS)
```
$ mkdir -p /tmp/fs-no-project && cd /tmp/fs-no-project
$ unset FLOWSTATE_CONFIG
$ timeout 10 flowstate server
No flowstate.toml found in /private/tmp/fs-no-project or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2
$ flowstate server 2>&1 | grep -i Traceback  # no match — no traceback
```

### TEST-8 (FAIL — only `check` is broken)
```
$ cd /tmp/fs-no-project
$ flowstate run flows/foo.flow
No flowstate.toml found in /private/tmp/fs-no-project or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2   ← PASS

$ flowstate check flows/foo.flow
Error: File not found: flows/foo.flow
exit=1   ← FAIL (expected exit 2 with "No flowstate.toml found")

$ flowstate runs
No flowstate.toml found in /private/tmp/fs-no-project or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2   ← PASS

$ flowstate status fake-id
No flowstate.toml found in /private/tmp/fs-no-project or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2   ← PASS
```

### TEST-9 (PASS)
```
$ mkdir -p /tmp/fs-no-project2 && cd /tmp/fs-no-project2
$ flowstate init
Created flowstate.toml and flows/example.flow.
exit=0
$ ls flows/example.flow    # exists
```

### TEST-10 (FAIL — `--version` missing)
```
$ cd /tmp/fs-no-project
$ flowstate --version
No such option: --version
exit=2   ← FAIL

$ flowstate version
No such command 'version'.
exit=2   ← FAIL

$ flowstate -v
No such option: -v
exit=2   ← FAIL

$ flowstate -V
No such option: -V
exit=2   ← FAIL

$ flowstate --help
 Usage: flowstate [OPTIONS] COMMAND [ARGS]...
 State-machine orchestration system for AI agents.
 --help   Show this message and exit.
 Commands: init, check, server, run, runs, status, schedules, trigger
exit=0   ← PASS (commands listed include init, server, check, run)
```

### TEST-11 (FAIL — strict reading)
```
$ mkdir -p /tmp/fs-polish-default && cd /tmp/fs-polish-default
$ flowstate init
$ nohup flowstate server --port 9093 > /tmp/fs-eval-312-data/server11.log 2>&1 &
pid=97883
$ curl -s http://127.0.0.1:9093/health
{"status":"ok","version":"0.1.0","project":{"slug":"fs-polish-default-65e05c4a","root":"/private/tmp/fs-polish-default"}}
$ cat server11.log
2026-04-11 21:17:13,228 WARNING flowstate.server.app: UI dist directory not found at .../ui/dist. Static file serving is disabled.   ← FAIL: WARNING token present
Starting Flowstate server on 127.0.0.1:9093
Project: /private/tmp/fs-polish-default (slug=fs-polish-default-65e05c4a)
INFO:     Uvicorn running on http://127.0.0.1:9093 (Press CTRL+C to quit)
$ grep -c '=====' server11.log   # 0 — host banner border absent (good)
$ grep -c 'Flowstate is binding to' server11.log   # 0 — host banner absent (good)
$ kill 97883
```
Host-warning banner intent satisfied; literal assertion fails due to pre-existing UI-dist WARNING log.

### TEST-12 (PASS)
```
$ cd /tmp/fs-polish-default
$ nohup flowstate server --host 0.0.0.0 --port 9094 > /tmp/fs-eval-312-data/server12.log 2>&1 &
pid=97983
$ curl -s http://127.0.0.1:9094/health   # → 200
$ cat server12.log
============================================================
WARNING: Flowstate is binding to 0.0.0.0:9094.
Flowstate v0.1 has NO AUTHENTICATION. Anyone who can reach
this address can execute code on this machine via Flowstate's
subprocess harnesses.
Only use non-loopback binds in trusted networks.
============================================================
2026-04-11 21:17:28,418 WARNING flowstate.server.app: UI dist directory not found ...
Starting Flowstate server on 0.0.0.0:9094
Project: /private/tmp/fs-polish-default (slug=fs-polish-default-65e05c4a)
INFO:     Uvicorn running on http://0.0.0.0:9094 (Press CTRL+C to quit)
$ grep -c '^=====' server12.log   # 2 — border present
$ grep -c 'WARNING: Flowstate is binding to 0.0.0.0:9094' server12.log   # 1
$ grep -c 'NO AUTHENTICATION' server12.log   # 1
$ grep -c 'Only use non-loopback binds in trusted networks' server12.log   # 1
$ kill 97983
```

### TEST-13 (PASS)
```
$ cd /tmp/fs-polish-default
$ nohup flowstate server --host 127.0.0.1 --port 9095 > /tmp/fs-eval-312-data/server13.log 2>&1 &
$ curl -s http://127.0.0.1:9095/health   # → 200
$ grep -c 'Flowstate is binding to' server13.log   # 0
$ grep -c '=====' server13.log              # 0
$ grep -c 'NO AUTHENTICATION' server13.log   # 0
$ kill <pid>
```

### TEST-14 (PASS)
```
$ mkdir -p /tmp/fs-health-a && cd /tmp/fs-health-a
$ flowstate init
$ nohup flowstate server --port 9096 > ... 2>&1 &
$ curl -s http://127.0.0.1:9096/health
{"status":"ok","version":"0.1.0","project":{"slug":"fs-health-a-deef337a","root":"/private/tmp/fs-health-a"}}
http_code=200
$ python -c "
import json
d=json.load(open('/tmp/fs-eval-312-data/health14.json'))
assert d['status']=='ok'
assert d['version']=='0.1.0'                 # PEP 440 accepted
assert d['project']['slug'].startswith('fs-health-a-')
assert d['project']['root'].endswith('fs-health-a')
assert sorted(d.keys())==['project','status','version']
assert sorted(d['project'].keys())==['root','slug']
"
$ kill <pid>
```

### TEST-15 (PASS)
```
$ mkdir -p /tmp/fs-health-b && cd /tmp/fs-health-b
$ flowstate init
$ nohup flowstate server --port 9096 > ... 2>&1 &
$ curl -s http://127.0.0.1:9096/health
{"status":"ok","version":"0.1.0","project":{"slug":"fs-health-b-d3f3ae02","root":"/private/tmp/fs-health-b"}}
$ python -c "
import json
a=json.load(open('/tmp/fs-eval-312-data/health14.json'))
b=json.load(open('/tmp/fs-eval-312-data/health15.json'))
assert a['version']==b['version']
assert a['project']['slug']!=b['project']['slug']
assert b['project']['slug'].startswith('fs-health-b-')
assert b['project']['root'].endswith('fs-health-b')
"
$ kill <pid>
```

### TEST-16 (PASS)
Covered by TEST-14's shape validation — only `status`, `version`, `project` at top level; only `slug`, `root` under project.

### TEST-17 (FAIL — strict reading, same UI-dist WARNING issue as TEST-11)
```
$ mkdir -p /tmp/fs-journey && cd /tmp/fs-journey
$ echo '{"name":"journey-demo"}' > package.json

STEP 1 — server before init
$ timeout 10 flowstate server
No flowstate.toml found in /private/tmp/fs-journey or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2   ← PASS

STEP 2 — init
$ flowstate init
Created flowstate.toml and flows/example.flow.
Next:
  flowstate check flows/example.flow
  flowstate server
exit=0
$ ls -la
  flowstate.toml
  flows/example.flow
  package.json
$ grep -E 'npm|node' flows/example.flow   # PRESENT (Node detection worked)

STEP 3 — check
$ flowstate check flows/example.flow
OK
exit=0

STEP 4 — server (bg)
$ nohup flowstate server --port 9097 > /tmp/fs-eval-312-data/journey.log 2>&1 &
pid=98162
$ cat journey.log
2026-04-11 21:18:16,491 WARNING flowstate.server.app: UI dist directory not found ...
Starting Flowstate server on 127.0.0.1:9097
Project: /private/tmp/fs-journey (slug=fs-journey-ac30af6a)
INFO:     Uvicorn running on http://127.0.0.1:9097 (Press CTRL+C to quit)
   ← FAIL (strict): stderr contains a `WARNING` token (UI-dist noise), even though host warning banner is absent.

STEP 5 — poll /health
$ curl -s http://127.0.0.1:9097/health
{"status":"ok","version":"0.1.0","project":{"slug":"fs-journey-ac30af6a","root":"/private/tmp/fs-journey"}}
http_code=200 (ready in 2 tries)

STEP 6 — shape validation
status == "ok"       ✓
project.slug starts "fs-journey-"  ✓  (fs-journey-ac30af6a)
project.root == "/private/tmp/fs-journey" (resolved absolute — accepted per macOS /tmp symlink note) ✓
version == "0.1.0" (non-empty) ✓

STEP 7 — SIGTERM
$ kill 98162
elapsed 0s   ← PASS (under 5s)

POLLUTION CHECK
- worktree: /Users/.../phase-31-deployability/flowstate.toml  (pre-existing, not created by this run)
- ~/.flowstate/flowstate.db: pre-existing (FLOWSTATE_DATA_DIR was /tmp/fs-eval-312-data)
- ~/.flowstate/projects/fs-journey*: NONE (correct — scratch data dir was used)
- /tmp/fs-eval-312-data/projects/fs-journey-ac30af6a: present (correct home)
```

## Summary

14 of 17 tests PASS. 3 FAILs:
- **TEST-8** — `check` exits 1, not 2, outside a project (either sprint contract or implementation must change).
- **TEST-10** — `flowstate --version` does not exist; immediate fix needed.
- **TEST-11 + TEST-17 step 4** — pre-existing UI-dist WARNING log violates literal "no WARNING in stderr" assertion, though the host warning banner (the actual target of the test) is correctly absent.

Verdict: **FAIL**.

The fundamental thesis of the sprint (the fresh-user journey: `init → check → server → /health`) does compose end-to-end. The host warning banner mechanics are tight. `/health` is correct, minimal, and project-aware. The four templates are all type-safe. But the three gaps above are real observable breakages against the sprint contract, and per "no benefit of the doubt" must be reported as FAIL until the domain agent either fixes them or the sprint-planner loosens the contract where appropriate.
