# [SERVER-023] CLI `flowstate run` assumes running server for artifact API

## Domain
server

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-022
- Blocks: —

## Spec References
- specs.md Section 9.6 — "API-Based Artifact Protocol"

## Summary
After the artifact API migration, agents need a running Flowstate server to POST artifacts. The `flowstate run` CLI command currently creates a `FlowExecutor` directly without a server. Update it to read `server_base_url` from the config (or `--server` CLI flag) and pass it to the executor. If the server isn't running, the agent's curl calls fail and the flow pauses with a clear error — no auto-start needed.

## Acceptance Criteria
- [ ] `flowstate run` reads server URL from config (`server.host` + `server.port`) or `--server` CLI flag
- [ ] `server_base_url` is passed to the executor
- [ ] If no server is configured, default to `http://127.0.0.1:9090`
- [ ] Clear error message if agent artifact POST fails (server not running)

## Technical Design

### Files to Create/Modify
- `src/flowstate/cli.py` — add `--server` option, pass `server_base_url` to executor

### Key Implementation Details

```python
@app.command()
def run(
    path: ...,
    server: Annotated[str | None, typer.Option(help="Flowstate server URL")] = None,
    ...
):
    server_base_url = server or f"http://{cfg.server_host}:{cfg.server_port}"
    # Pass to executor...
```

### Edge Cases
- Server not running: agent curl fails, flow pauses with "artifact submission failed" — user starts server and retries

## Testing Strategy
- Verify CLI passes server_base_url to executor

## E2E Verification Plan

### Verification Steps
1. Start server: `uv run flowstate server`
2. In another terminal: `uv run flowstate run flows/simple.flow`
3. Verify flow completes (agents POST artifacts to running server)

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
