# [ENGINE-082] Executor: derive subprocess `FLOWSTATE_SERVER_URL` from `Project`

## Domain
engine

## Status
done

**Eval verdict: PASS (issues/evals/sprint-phase-32-eval.md, batch-level)**

## Priority
P1 (important)

## Dependencies
- Depends on: SHARED-007, SERVER-026
- Blocks: —

## Spec References
- specs.md §13.4 Deployment & Installation — "Security posture (v0.1)"
- specs.md §9 Claude Code Integration

## Summary
`src/flowstate/engine/executor.py:2606` injects `"FLOWSTATE_SERVER_URL": self._server_base_url or "http://127.0.0.1:9090"` into every task subprocess's environment. The fallback is what subprocesses use to call back into Flowstate's artifact API and other server endpoints. If two Flowstate servers are running concurrently — e.g., a dev server on 9090 and a deployed server on 9091, or two scratch projects on different ports — a subprocess of the 9091 server will incorrectly call back to 9090 because its `_server_base_url` was never wired in. The hardcoded fallback silently routes the call to the wrong process.

Fix: derive the URL from the actual `(host, port)` the server bound to (sourced from `Project.config.server_host` / `server_port`), and either pass it down through the executor construction chain or fail loudly when neither is available — never fall through to a hardcoded port.

## Acceptance Criteria
- [ ] `FlowExecutor` (or whatever holds `_server_base_url`) is constructed with an explicit `server_base_url: str` derived from the running server's bound port.
- [ ] The hardcoded `"http://127.0.0.1:9090"` literal is removed from `executor.py:2606`.
- [ ] If `_server_base_url` is somehow `None` at the point of subprocess spawn, raise a typed error (`FlowExecutorConfigError` or similar) with a clear message — do **not** silently fall through to a guessed URL.
- [ ] When the server binds non-loopback (`--host 0.0.0.0`), the subprocess URL still uses `127.0.0.1` (subprocesses run on the same machine and loopback is the safest callback target).
- [ ] New unit test in `tests/engine/test_executor.py` (small sub-class only — avoid the deadlocking class): construct an executor with `server_base_url="http://127.0.0.1:9091"` and assert the env passed to a subprocess contains `FLOWSTATE_SERVER_URL=http://127.0.0.1:9091`.
- [ ] New unit test: `server_base_url=None` raises the typed error at subprocess-spawn time.

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — replace the `or "http://..."` fallback with explicit None-handling.
- `src/flowstate/server/app.py` — when constructing the `FlowExecutor` (or its factory), pass `f"http://127.0.0.1:{project.config.server_port}"`.
- `src/flowstate/server/queue_manager.py` — same plumbing if the executor is constructed there.
- `tests/engine/test_executor.py` — small class with the two unit tests (to avoid the unrelated `TestContextModeHandoff` deadlock).

### Key Implementation Details
```python
# executor.py around line 2606
if self._server_base_url is None:
    raise FlowExecutorConfigError(
        "FlowExecutor was not given a server_base_url; cannot wire "
        "FLOWSTATE_SERVER_URL into the subprocess environment. This "
        "indicates a wiring bug between create_app and the executor."
    )

env = {
    ...
    "FLOWSTATE_SERVER_URL": self._server_base_url,
    ...
}
```

In `app.py` (or wherever the executor is constructed):
```python
server_base_url = f"http://127.0.0.1:{project.config.server_port}"
executor = FlowExecutor(
    ...
    server_base_url=server_base_url,
    ...
)
```

### Edge Cases
- `--port` CLI flag overrides `project.config.server_port`. The wiring must use the **effective** port the server actually bound to. Source it from the same place the warning banner reads, not from raw config.
- IPv6 binds (`::`, `::1`) — still callback via `127.0.0.1` (loopback IPv4 is universally available on macOS/Linux dev machines).
- A server bound to a hostname (e.g., `flowstate.local`) is out of scope for v0.1 (loopback-only model). The fix can assume loopback.

## Testing Strategy
- Unit tests as listed in the acceptance criteria — both must use a `-k` filter to avoid the unrelated `TestContextModeHandoff::test_context_mode_handoff_with_summary` deadlock in `test_executor.py`.
- Manual verification: start two servers on different ports (9090, 9091), trigger a flow on each, verify subprocess env (via `ps -E <pid>` or by adding a `print(os.environ.get("FLOWSTATE_SERVER_URL"))` in a temporary test harness).

## E2E Verification Plan

### Verification Steps
1. Scratch project with a single trivial flow whose task prints `os.environ.get("FLOWSTATE_SERVER_URL")` to stderr (mock harness OK).
2. Start server on port 9091 from the project.
3. Trigger the flow.
4. Inspect the captured subprocess env. Assert `FLOWSTATE_SERVER_URL == "http://127.0.0.1:9091"`, NOT `:9090`.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] Hardcoded `"http://127.0.0.1:9090"` removed from executor
- [ ] `FlowExecutorConfigError` raised on missing URL
- [ ] `app.py` wires the effective bound port
- [ ] 2 unit tests passing
- [ ] `/test` passes
- [ ] `/lint` passes
- [ ] E2E steps above verified
