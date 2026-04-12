# [SERVER-030] Loud warning on non-127.0.0.1 bind; default host to 127.0.0.1

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-026
- Blocks: —

## Spec References
- specs.md §13.4 Deployment & Installation — "Security posture (v0.1)"

## Summary
Flowstate v0.1 has no authentication. Exposing it on a non-loopback interface means anyone who can reach the port can start/stop runs, read logs, and execute code via the Claude Code subprocesses. This issue ensures the default is `127.0.0.1` and that binding to anything else prints a prominent, unmissable warning.

## Acceptance Criteria
- [x] Default `host` in the `flowstate.toml` schema and the `flowstate server` CLI is `127.0.0.1`.
- [x] When the resolved host is anything other than `127.0.0.1`, `localhost`, or `::1`, a multi-line warning is printed to stderr at startup:
  ```
  ============================================================
  WARNING: Flowstate is binding to <host>:<port>.
  Flowstate v0.1 has NO AUTHENTICATION. Anyone who can reach
  this address can execute code on this machine via Flowstate's
  subprocess harnesses.
  Only use non-loopback binds in trusted networks.
  ============================================================
  ```
- [x] The warning is printed once, at server startup, before the ASGI loop runs.
- [x] The warning is not printed for loopback addresses.
- [x] A test captures stderr output during startup and asserts the warning's presence/absence.

## Technical Design

### Files to Create/Modify
- `src/flowstate/cli.py` — in the `server` command, after resolving host/port, check and print the warning.
- `src/flowstate/server/app.py` — if startup logic is triggered via the app factory, add the check there too. Prefer a single source of truth: put the check in `cli.py::server`, since only the CLI path configures the bind address.
- `tests/server/test_cli_server_warning.py` — new test.

### Key Implementation Details
```python
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

def _warn_if_non_loopback(host: str, port: int) -> None:
    if host in LOOPBACK_HOSTS:
        return
    border = "=" * 60
    msg = (
        f"{border}\n"
        f"WARNING: Flowstate is binding to {host}:{port}.\n"
        f"Flowstate v0.1 has NO AUTHENTICATION. Anyone who can reach\n"
        f"this address can execute code on this machine via Flowstate's\n"
        f"subprocess harnesses.\n"
        f"Only use non-loopback binds in trusted networks.\n"
        f"{border}"
    )
    typer.echo(msg, err=True)
```

### Edge Cases
- `0.0.0.0` → warning fires.
- `::` → warning fires (IPv6 wildcard).
- User passes `--host 127.0.0.1` explicitly → no warning.
- `host` resolved from TOML config → same check applies.

## Testing Strategy
- Unit test using `typer.testing.CliRunner` invoking `flowstate server --host 0.0.0.0 --port 9999` with a dry-run flag (or a short-lived event loop) and asserting the warning is present in stderr.
- Unit test with `--host 127.0.0.1`: warning is absent.
- Keep the test fast by not actually starting the HTTP server (factor the warning check into a pure function and test it directly).

## E2E Verification Plan

### Verification Steps
1. `flowstate server --host 0.0.0.0` in a project → stderr shows the warning banner immediately. Server then starts as normal.
2. `flowstate server` (default) → no warning.
3. `flowstate server --host 127.0.0.1` → no warning.

## E2E Verification Log

### Post-Implementation Verification (2026-04-11)

Canonical TEST-17 journey executed against the real CLI. Full transcript
shared across SERVER-028/029/030/031:

```
===== STEP 1: SERVER-029 outside project (expect exit 2) =====
exit_code=2
No flowstate.toml found in / or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
STEP 1 PASS

===== STEP 2: SERVER-028 init with Node detection =====
Created flowstate.toml and flows/example.flow.
STEP 2 PASS

===== STEP 3: SERVER-028 check passes on scaffolded flow =====
OK
STEP 3 PASS

===== STEP 4: SERVER-031 /health endpoint (default host, no warning) =====
server pid=95999
ready after 2s
{"status":"ok","version":"0.1.0","project":{"slug":"fs-phase312-proj-687706de","root":"/private/tmp/fs-phase312-proj"}}
# asserted: grep -q WARNING server.log  =>  NOT present
STEP 4 PASS

===== STEP 5: SERVER-030 non-loopback warning (--host 0.0.0.0 --port 9098) =====
server pid=96073
---warn.log---
============================================================
WARNING: Flowstate is binding to 0.0.0.0:9098.
Flowstate v0.1 has NO AUTHENTICATION. Anyone who can reach
this address can execute code on this machine via Flowstate's
subprocess harnesses.
Only use non-loopback binds in trusted networks.
============================================================
2026-04-11 21:08:47,237 WARNING flowstate.server.app: UI dist directory not found at ...
Starting Flowstate server on 0.0.0.0:9098
Project: /private/tmp/fs-phase312-proj (slug=fs-phase312-proj-687706de)
INFO:     Started server process [96075]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:9098 (Press CTRL+C to quit)
---end---
server reachable on 9098
STEP 5 PASS

===== STEP 6/7: init idempotence & --force (SERVER-028) =====
# (omitted here — covered in 028 issue log)

ALL STEPS PASSED
```

The three SERVER-030 invariants are explicitly asserted:

1. **STEP 4 (default host)**: `flowstate server --port 9097` (no
   `--host` flag, defaults to `127.0.0.1`) produced no banner in the
   server log. `grep -q WARNING` returns non-zero. Verified via
   `! grep -q WARNING /tmp/fs-phase312-server.log` in the script.
2. **STEP 5 (explicit `0.0.0.0`)**: The banner fires exactly once,
   contains the strings `WARNING`, `NO AUTHENTICATION`, `0.0.0.0:9098`,
   and `Only use non-loopback binds in trusted networks`, and uses a
   row of 60 `=` characters as the border. The banner appears **before**
   uvicorn's "Uvicorn running on ..." line, proving it is emitted prior
   to the ASGI loop accepting connections. The server then reaches a
   healthy state on `0.0.0.0:9098` (reachable from loopback `curl`).
3. **Loopback allow-list**: verified in
   `tests/server/test_cli_server_warning.py` for `127.0.0.1`,
   `localhost`, `::1`, and also for `::` (IPv6 wildcard, warns) and
   `192.168.1.10` (routable, warns). All 10 tests pass.

## Completion Checklist
- [x] Default host set to `127.0.0.1`
- [x] `_warn_if_non_loopback` implemented
- [x] Warning wired into `flowstate server`
- [x] Unit test passing
- [x] `/lint` passes
- [x] E2E steps above verified
