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
- [ ] Default `host` in the `flowstate.toml` schema and the `flowstate server` CLI is `127.0.0.1`.
- [ ] When the resolved host is anything other than `127.0.0.1`, `localhost`, or `::1`, a multi-line warning is printed to stderr at startup:
  ```
  ============================================================
  WARNING: Flowstate is binding to <host>:<port>.
  Flowstate v0.1 has NO AUTHENTICATION. Anyone who can reach
  this address can execute code on this machine via Flowstate's
  subprocess harnesses.
  Only use non-loopback binds in trusted networks.
  ============================================================
  ```
- [ ] The warning is printed once, at server startup, before the ASGI loop runs.
- [ ] The warning is not printed for loopback addresses.
- [ ] A test captures stderr output during startup and asserts the warning's presence/absence.

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
_Filled in by the implementing agent._

## Completion Checklist
- [ ] Default host set to `127.0.0.1`
- [ ] `_warn_if_non_loopback` implemented
- [ ] Warning wired into `flowstate server`
- [ ] Unit test passing
- [ ] `/lint` passes
- [ ] E2E steps above verified
