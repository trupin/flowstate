# [ENGINE-044] AcpHarness subprocess stalls silently — no output, no error logs

## Domain
engine

## Status
done

## Priority
P1

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 4 — "Execution Engine"

## Summary
Found during E2E testing (suite: linear). When the QueueManager picks up a task and the executor starts via AcpHarness, the claude subprocess is spawned but never produces any StreamEvent output. Three compounding root causes:

1. **Subprocess environment stripped**: The ACP library's `default_environment()` only passes `HOME, PATH, SHELL, TERM, USER, LOGNAME`. `ANTHROPIC_API_KEY` and config paths are not inherited, so Claude can't authenticate and hangs during startup.
2. **No timeouts on ACP RPC calls**: `conn.initialize()`, `conn.new_session()` etc. await forever if the subprocess never responds.
3. **No Python logging configured**: The CLI `server` command never calls `logging.basicConfig()`, so all `flowstate.*` logger output is silently discarded.

## Acceptance Criteria
- [x] The CLI `server` command configures Python logging so flowstate.* logger output appears in the console
- [x] AcpHarness passes critical env vars (ANTHROPIC_API_KEY, CLAUDE_CONFIG_DIR, XDG_CONFIG_HOME) to subprocess
- [x] AcpHarness.start_session() and _run_acp_session() have timeouts on initialize/new_session (30s/15s)
- [x] Subprocess health check after spawn detects immediate exits
- [x] Error events propagate to executor when harness fails
- [x] Bridge client logs errors that would be swallowed by ACP library

## Technical Design

### Files Modified
- `src/flowstate/cli.py` — Added `logging.basicConfig()` in `server()` using config's `log_level`
- `src/flowstate/engine/acp_client.py` — Added `_build_subprocess_env()`, timeout constants, `asyncio.wait_for()` wrappers, subprocess health check, bridge error logging
- `tests/engine/test_acp_client.py` — Added 7 tests for env, health check, and timeouts

### Key Implementation Details
- `_build_subprocess_env()` merges critical env vars from `os.environ` into the env dict passed to `spawn_agent_process`
- `_ACP_INIT_TIMEOUT = 30.0` for initialize, `_ACP_SESSION_TIMEOUT = 15.0` for session create/load
- No timeout on `conn.prompt()` — long-running tasks are bounded by the budget system
- Health check: `await asyncio.sleep(0.1)` then check `process.returncode`

## Testing Strategy
- `uv run pytest tests/engine/test_acp_client.py` — 56 tests pass
- Re-run `/e2e linear` to verify flow completes end-to-end

## Completion Checklist
- [x] Unit tests written and passing
- [x] `/lint` passes (ruff, pyright)
- [x] Acceptance criteria verified
