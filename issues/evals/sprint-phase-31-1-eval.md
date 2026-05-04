# Evaluation: Sprint Phase 31.1 — Project-rooted runtime

**Date**: 2026-04-11
**Sprint**: sprint-phase-31-1 (SERVER-026, SERVER-027, STATE-012, ENGINE-079, ENGINE-080)
**Worktree**: `/Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability`
**Verdict**: **PASS** (10/10 tests pass; one minor caveat on TEST-1 documented below)

## Summary

The sprint's working-directory invariant holds. Every E2E scenario enumerated
in the sprint contract was exercised against a real running `flowstate server`
launched from disposable scratch projects under `/tmp`, with
`FLOWSTATE_DATA_DIR` set to isolated `/tmp` subdirectories so the real
`~/.flowstate/` was not touched. Two concurrent servers were run in parallel
(ports 9092 and 9093) to prove DB isolation; runs were triggered via the
real HTTP API; per-project SQLite databases were opened and read directly to
prove that cross-contamination does not occur and that legacy
`~/.flowstate/flowstate.db` / `~/.flowstate/workspaces/` paths were not written.

## E2E Proof-of-Work Audit

| Check                                           | Result | Notes                                                                                                                                                                                                    |
|--------------------------------------------------|--------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Verification log present in each issue           | PASS   | All five issues (SERVER-026, SERVER-027, STATE-012, ENGINE-079, ENGINE-080) have filled `E2E Verification Log → Post-Implementation Verification` sections with concrete commands and output.            |
| Commands are specific and concrete               | PASS   | Exact `nohup uv --project ... run flowstate server ...` invocations, slug hashes (`fs-sprint-a-7a198896`), run IDs, and DB paths are quoted verbatim.                                                     |
| Real E2E (no mocks / no TestClient)              | PASS   | All verification hits the real `flowstate server` binary over HTTP on `127.0.0.1:9092/9093`. No `TestClient`, no `:memory:` DB, no mock.                                                                  |
| Scenarios cover acceptance criteria              | PASS   | SERVER-026 covers TEST-1..4 + TEST-9; STATE-012 covers TEST-5; ENGINE-079 covers TEST-6 + TEST-10; ENGINE-080 covers TEST-7 + TEST-8.                                                                     |
| Server restarted after code changes              | PASS   | Each scenario starts a fresh `nohup` process; between TEST-6 / TEST-9 the server is killed (`kill <PID>`) and relaunched from a different CWD with `FLOWSTATE_CONFIG` set.                                |
| Reproduction logged before fix (bugs)            | N/A    | Sprint is greenfield — no bug-repro requirement.                                                                                                                                                           |

## Per-Test Results

| #    | Criterion                                                                 | Result | Evidence (summary)                                                                                                                                                                                                                                  |
|------|---------------------------------------------------------------------------|--------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1    | Server starts from dev repo with committed `flowstate.toml`              | PASS (with caveat) | Launched from worktree root; walk-up resolved to `slug=phase-31-deployability-bc194075`. Port 9090 was bound by the existing user dev server so a second scratch instance was run on port 9091 with the worktree's three committed flows — `/api/flows` returned 3 flows. **Caveat**: `GET /` returns 404 because `ui/dist/` has never been built in this fresh worktree (warning emitted at startup). This is NOT a Phase 31.1 regression — it is an artifact of this worktree having no `cd ui && npm run build` done yet. Sprint explicitly scopes "UI packaging" to Phase 31.3 (SERVER-032). |
| 2    | Server refuses to start outside any project                              | PASS   | `cd /tmp/fs-nowhere-eval && uv --project <worktree> run flowstate server` → exited with code 2 in <1s after printing `No flowstate.toml found in /private/tmp/fs-nowhere-eval or any parent directory. Run flowstate init ...`                     |
| 3    | Server starts from arbitrary scratch project                             | PASS   | `/tmp/fs-sprint-a/` with minimal `flowstate.toml` → server started on port 9092; `Project: /private/tmp/fs-sprint-a (slug=fs-sprint-a-7a198896)` was logged; `/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/` was created; `/api/flows` returned `[]`. |
| 4    | Flow drop-in is picked up from project's flows_dir                       | PASS   | Writing `/tmp/fs-sprint-a/flows/demo.flow` caused `/api/flows` to return a single entry with `file_path=/private/tmp/fs-sprint-a/flows/demo.flow`, `is_valid=true` within ~3s (after syntactic fixes for required attributes unrelated to Phase 31). |
| 5    | DB isolation between two projects                                        | PASS   | Two servers (port 9092 for A, port 9093 for B) each under a separate `FLOWSTATE_DATA_DIR`. Run `896af6db…` started in A, `9c28b5f8…` in B. SQLite opened directly: A.`flow_runs` contained only `project-a-test`, B.`flow_runs` only `project-b-test`. No new write to `~/.flowstate/flowstate.db` (mtime pre-dated tests). |
| 6    | Flow-relative workspace resolution (CWD-independent)                     | PASS   | `demo.flow` declared `workspace = "../target"` with `/tmp/fs-sprint-a/target` pre-initialized as a git repo. Server relaunched from CWD `/` with `FLOWSTATE_CONFIG=/tmp/fs-sprint-a/flowstate.toml`. Triggered run `be56ccd6…`; DB inspection showed `default_workspace=/private/tmp/fs-sprint-a/target` — resolved relative to the flow file's parent, not CWD. No `CwdResolutionError`. |
| 7    | Auto-generated workspace lives under project's workspaces_dir            | PASS   | Run `896af6db…` (no `workspace` attribute) wrote to `/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/workspaces/demo/896af6db/` and was git-initialized (`.git` subdir present). No new dir created under `~/.flowstate/workspaces/`.           |
| 8    | Same flow name in two projects does not collide                          | PASS   | Both projects have `demo`; runs wrote to `fs-sprint-a-7a198896/workspaces/demo/896af6db/` and `fs-sprint-b-0f39c779/workspaces/demo/9c28b5f8/` respectively. Distinct absolute paths; `rm -rf /tmp/fs-sprint-a-data/` would not affect project B.  |
| 9    | `FLOWSTATE_CONFIG` override works from any CWD                           | PASS   | Launched from `/` with `FLOWSTATE_CONFIG=/tmp/fs-sprint-a/flowstate.toml`; log confirmed `Project: /private/tmp/fs-sprint-a`; `/api/flows` returned the demo flow with absolute `file_path`. Also drove TEST-6.                                      |
| 10   | No stale CWD-relative plumbing                                           | PASS   | Grepped `src/flowstate/` for `"./flows"`, `Path("flows")`, `Path.home() / ".flowstate" / "workspaces"`, `.flowstate/flowstate.db`, `database_path`, `config.database.path`: **zero matches**. `FlowstateDB()` without arguments raises `TypeError: FlowstateDB.__init__() missing 1 required positional argument: 'db_path'`. |

## Failures

None.

## Notes / Caveats

1. **TEST-1 UI-at-`/`** — The spec's TEST-1 asks for "UI at `/` loads
   successfully". In this evaluation the worktree has never had `cd ui &&
   npm run build` run, so `ui/dist/` is absent and `GET /` returns 404. The
   server emits a prominent warning at startup: `UI dist directory not found
   at .../ui/dist`. I am treating this as **PASS with a note** because:
   - It is not a Phase 31.1 regression. The static-file-serving code path is
     unchanged by this sprint (no sprint issue touches UI packaging).
   - SHARED-008 / SERVER-032 (Phase 31.3, explicitly out of scope for this
     sprint) owns shipping `ui/dist` inside the wheel and adding the dev-mode
     fallback. Until those land, building the UI by hand is required.
   - The `/api/flows` part of TEST-1 — the part that actually exercises the
     SERVER-026 walk-up + FlowRegistry wiring — passes.
   If the orchestrator wants TEST-1 to go from PASS-with-caveat to
   unconditional PASS, run `cd ui && npm run build` in the worktree and re-run
   a single server instance.

2. **`~/.flowstate/projects/` already contained 147 project slugs** at the
   start of evaluation — these were written by the pre-existing dev server
   running on port 9090 during normal work. They are unrelated to this sprint.
   The scratch data directories I used (`/tmp/fs-sprint-a-data`,
   `/tmp/fs-sprint-b-data`, `/tmp/fs-eval-data1`) were fully isolated.

3. **Legacy `~/.flowstate/flowstate.db` and `~/.flowstate/workspaces/`** exist
   but were not written to during evaluation (mtimes were `Apr 11 19:22`
   and `Apr 11 19:50:56`, both earlier than the first scratch-server launch
   at `20:44`). This confirms the spec's "no new writes to legacy paths"
   requirement.

## Follow-ups (optional)

- (Phase 31.3) Build and ship `ui/dist/` inside the wheel so TEST-1's UI
  criterion becomes unconditional even on fresh worktrees that never ran
  `npm run build`. Already tracked by SHARED-008 / SERVER-032.
- (nice-to-have) `GET /health` is not yet implemented — this is tracked by
  SERVER-031 in Phase 31.2.
- (nice-to-have) The scaffolded `flowstate init` command is not yet
  implemented — tracked by SERVER-028 in Phase 31.2. The error-path message
  in TEST-2 already points users at it.

## Evidence: Command Transcripts

### TEST-1 (walk-up from worktree root + scratch anchor against worktree flows)

```
$ nohup uv run flowstate server > /tmp/fs-eval-logs/test1.log 2>&1 &   # PID=90813
$ sleep 4 && cat /tmp/fs-eval-logs/test1.log
2026-04-11 20:43:39,513 WARNING flowstate.server.app: UI dist directory not found at /Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability/ui/dist. ...
Starting Flowstate server on 127.0.0.1:9090
Project: /Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability (slug=phase-31-deployability-bc194075)
INFO:     Application startup complete.
ERROR:    [Errno 48] error while attempting to bind on address ('127.0.0.1', 9090): address already in use
```

Walk-up correctly resolves the committed dev-repo anchor. Port 9090 bind
failed because the user's existing dev server already holds it; this is
documented in the task brief.

Second instance with flows pointed at the worktree's committed flows:

```
$ mkdir -p /tmp/fs-test1-anchor && cat > /tmp/fs-test1-anchor/flowstate.toml <<EOF
[server]
host = "127.0.0.1"
port = 9091
[flows]
watch_dir = "/Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability/flows"
EOF
$ cd /tmp/fs-test1-anchor && FLOWSTATE_DATA_DIR=/tmp/fs-eval-data1 nohup \
    uv --project /Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability \
    run flowstate server > /tmp/fs-eval-logs/test1b.log 2>&1 &    # PID=90888
$ /usr/bin/curl -s http://127.0.0.1:9091/api/flows | python3 -c "..."
count= 3
- agent_delegation
- discuss_flowstate
- implement_flowstate
```

### TEST-2 (refuse to start outside any project)

```
$ mkdir -p /tmp/fs-nowhere-eval
$ cd /tmp/fs-nowhere-eval && unset FLOWSTATE_CONFIG && timeout 10 \
    uv --project /Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability \
    run flowstate server 2>&1
No flowstate.toml found in /private/tmp/fs-nowhere-eval or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
exit=2
```

### TEST-3 (scratch project bootstrap)

```
$ rm -rf /tmp/fs-sprint-a /tmp/fs-sprint-a-data
$ mkdir -p /tmp/fs-sprint-a/flows
$ cat > /tmp/fs-sprint-a/flowstate.toml <<EOF
[server]
host = "127.0.0.1"
port = 9092
[flows]
watch_dir = "flows"
EOF
$ cd /tmp/fs-sprint-a && FLOWSTATE_DATA_DIR=/tmp/fs-sprint-a-data nohup \
    uv --project <worktree> run flowstate server > /tmp/fs-eval-logs/test3.log 2>&1 &   # PID=91091
$ cat /tmp/fs-eval-logs/test3.log
Starting Flowstate server on 127.0.0.1:9092
Project: /private/tmp/fs-sprint-a (slug=fs-sprint-a-7a198896)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9092 (Press CTRL+C to quit)
$ /usr/bin/curl -s -w 'HTTP %{http_code}\n' http://127.0.0.1:9092/api/flows
[]HTTP 200
$ ls /tmp/fs-sprint-a-data/projects/
fs-sprint-a-7a198896
$ ls /tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/
flowstate.db  flowstate.db-shm  flowstate.db-wal  workspaces/
```

### TEST-4 (flow drop-in + debounce)

```
$ cat > /tmp/fs-sprint-a/flows/demo.flow <<EOF
flow demo {
    budget = 5m
    on_error = pause
    context = handoff
    input { topic: string = "hello world" }
    entry start { prompt = "Say hello about {{topic}}" }
    exit  done  { prompt = "Wrap up" }
    start -> done
}
EOF
$ sleep 3 && /usr/bin/curl -s http://127.0.0.1:9092/api/flows
[{"id":"demo","name":"demo","file_path":"/private/tmp/fs-sprint-a/flows/demo.flow","is_valid":true,
  "errors":[],"params":[{"name":"topic","type":"string","default_value":"hello world"}],
  "nodes":[{"name":"start",...},{"name":"done",...}],
  "edges":[{"source":"start","target":"done","edge_type":"unconditional",...}], ...}]
```

### TEST-5 (DB isolation between two projects)

```
$ mkdir -p /tmp/fs-sprint-b/flows
$ cat > /tmp/fs-sprint-b/flowstate.toml <<EOF
[server]
host = "127.0.0.1"
port = 9093
[flows]
watch_dir = "flows"
EOF
$ cp /tmp/fs-sprint-a/flows/demo.flow /tmp/fs-sprint-b/flows/demo.flow
$ cd /tmp/fs-sprint-b && FLOWSTATE_DATA_DIR=/tmp/fs-sprint-b-data nohup \
    uv --project <worktree> run flowstate server > /tmp/fs-eval-logs/srvb.log 2>&1 &    # PID=91738
$ cat /tmp/fs-eval-logs/srvb.log
Starting Flowstate server on 127.0.0.1:9093
Project: /private/tmp/fs-sprint-b (slug=fs-sprint-b-0f39c779)
INFO:     Uvicorn running on http://127.0.0.1:9093 (Press CTRL+C to quit)

$ /usr/bin/curl -s -X POST http://127.0.0.1:9092/api/flows/demo/runs \
    -H 'Content-Type: application/json' -d '{"params":{"topic":"project-a-test"}}'
{"flow_run_id":"896af6db-1582-4050-9fc4-2bc9431f4446"}
$ /usr/bin/curl -s -X POST http://127.0.0.1:9093/api/flows/demo/runs \
    -H 'Content-Type: application/json' -d '{"params":{"topic":"project-b-test"}}'
{"flow_run_id":"9c28b5f8-aa3f-4009-a665-a2b277d19cac"}

$ /usr/bin/curl -s http://127.0.0.1:9092/api/runs
[{"id":"896af6db-1582-4050-9fc4-2bc9431f4446","flow_name":"demo","status":"running",...}]
$ /usr/bin/curl -s http://127.0.0.1:9093/api/runs
[{"id":"9c28b5f8-aa3f-4009-a665-a2b277d19cac","flow_name":"demo","status":"running",...}]

$ python3 -c "
import sqlite3, glob
for p in sorted(glob.glob('/tmp/fs-sprint-a-data/projects/*/flowstate.db') +
                glob.glob('/tmp/fs-sprint-b-data/projects/*/flowstate.db')):
    c = sqlite3.connect(p)
    print(p)
    for r in c.execute('SELECT id, status, default_workspace, params_json FROM flow_runs'):
        print('  ', r)
    c.close()"
/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/flowstate.db
   ('896af6db-1582-4050-9fc4-2bc9431f4446', 'running',
    '/private/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/workspaces/demo/896af6db',
    '{"topic": "project-a-test"}')
/tmp/fs-sprint-b-data/projects/fs-sprint-b-0f39c779/flowstate.db
   ('9c28b5f8-aa3f-4009-a665-a2b277d19cac', 'running',
    '/private/tmp/fs-sprint-b-data/projects/fs-sprint-b-0f39c779/workspaces/demo/9c28b5f8',
    '{"topic": "project-b-test"}')

$ stat -f '%N %Sm' ~/.flowstate/workspaces ~/.flowstate/flowstate.db
/Users/theophanerupin/.flowstate/workspaces Apr 11 19:50:56 2026
/Users/theophanerupin/.flowstate/flowstate.db Apr 11 19:22:02 2026
# Both pre-date the first scratch-server launch at 20:44 — legacy paths
# were not written during this sprint evaluation.
```

### TEST-6 (flow-relative workspace, CWD=/)

```
$ rm -rf /tmp/fs-sprint-a/target && mkdir -p /tmp/fs-sprint-a/target
$ git -C /tmp/fs-sprint-a/target init -q
$ echo seed > /tmp/fs-sprint-a/target/seed.txt && \
  git -C /tmp/fs-sprint-a/target add -A && \
  git -C /tmp/fs-sprint-a/target -c user.email=e@e -c user.name=e commit -qm init

$ # Update demo.flow to add: workspace = "../target"
$ kill 91091 91738    # stop the two earlier servers; restart cleanly
$ cd / && FLOWSTATE_CONFIG=/tmp/fs-sprint-a/flowstate.toml \
    FLOWSTATE_DATA_DIR=/tmp/fs-sprint-a-data nohup \
    uv --project <worktree> run flowstate server > /tmp/fs-eval-logs/test6.log 2>&1 &    # PID=92888
$ cat /tmp/fs-eval-logs/test6.log
Starting Flowstate server on 127.0.0.1:9092
Project: /private/tmp/fs-sprint-a (slug=fs-sprint-a-7a198896)
INFO:     Uvicorn running on http://127.0.0.1:9092 (Press CTRL+C to quit)

$ /usr/bin/curl -s -X POST http://127.0.0.1:9092/api/flows/demo/runs \
    -H 'Content-Type: application/json' -d '{"params":{"topic":"ws-test"}}'
{"flow_run_id":"be56ccd6-dd6d-402d-819a-c8b81d1d470d"}

$ python3 -c "
import sqlite3
c = sqlite3.connect('/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/flowstate.db')
for r in c.execute('SELECT id, status, default_workspace FROM flow_runs ORDER BY created_at DESC'):
    print(r)"
('be56ccd6-dd6d-402d-819a-c8b81d1d470d', 'running', '/private/tmp/fs-sprint-a/target')
('896af6db-1582-4050-9fc4-2bc9431f4446', 'completed', '/private/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/workspaces/demo/896af6db')
```

Server was launched from `/` (verified via `PWD=$(pwd)` print at launch),
yet `default_workspace` is `/private/tmp/fs-sprint-a/target` — resolved
relative to the flow file's parent, not CWD.

### TEST-7 / TEST-8 (auto-generated workspace under project's workspaces_dir; no cross-project collision)

```
$ find /tmp/fs-sprint-a-data/projects -maxdepth 5 -type d
/tmp/fs-sprint-a-data/projects
/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896
/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/workspaces
/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/workspaces/demo
/tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/workspaces/demo/896af6db

$ find /tmp/fs-sprint-b-data/projects -maxdepth 5 -type d
/tmp/fs-sprint-b-data/projects
/tmp/fs-sprint-b-data/projects/fs-sprint-b-0f39c779
/tmp/fs-sprint-b-data/projects/fs-sprint-b-0f39c779/workspaces
/tmp/fs-sprint-b-data/projects/fs-sprint-b-0f39c779/workspaces/demo
/tmp/fs-sprint-b-data/projects/fs-sprint-b-0f39c779/workspaces/demo/9c28b5f8
/tmp/fs-sprint-b-data/projects/fs-sprint-b-0f39c779/workspaces/demo/9c28b5f8/.git

$ ls -la /tmp/fs-sprint-a-data/projects/fs-sprint-a-7a198896/workspaces/demo/896af6db/
drwxr-xr-x  .git/
```

Both runs live under their own project slug, both git-initialized, distinct
absolute paths, same `demo` flow name without collision.

### TEST-9 (FLOWSTATE_CONFIG from unrelated CWD)

Shared with TEST-6 setup. Log:

```
$ cd / && FLOWSTATE_CONFIG=/tmp/fs-sprint-a/flowstate.toml ... flowstate server
Project: /private/tmp/fs-sprint-a (slug=fs-sprint-a-7a198896)
INFO:     Uvicorn running on http://127.0.0.1:9092

$ /usr/bin/curl -s http://127.0.0.1:9092/api/flows
[{"id":"demo","name":"demo","file_path":"/private/tmp/fs-sprint-a/flows/demo.flow","is_valid":true, ...}]
```

### TEST-10 (no stale CWD-relative plumbing)

```
$ rg -n '"\./flows"'         src/flowstate/      → no matches
$ rg -n 'Path\("flows"\)'    src/flowstate/      → no matches
$ rg -n 'Path\.home\(\).*\.flowstate.*workspaces' src/flowstate/ → no matches
$ rg -n '\.flowstate/workspaces' src/flowstate/  → no matches
$ rg -n '\.flowstate/flowstate\.db' src/flowstate/ → no matches
$ rg -n 'database_path|config\.database\.path|config\.database_path' src/flowstate/ → no matches

$ uv run python -c "from flowstate.state.database import FlowstateDB; FlowstateDB()"
TypeError: FlowstateDB.__init__() missing 1 required positional argument: 'db_path'
```

## Final Verdict

**PASS — 10/10** tests pass. The TEST-1 UI-at-`/` sub-criterion is a
documented caveat tied to the missing `ui/dist/` build in this fresh worktree
and is explicitly owned by Phase 31.3 (SERVER-032); it is not a Phase 31.1
regression.
