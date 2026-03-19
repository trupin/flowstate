# [E2E-013] Test: WebSocket Reconnection

## Domain
e2e

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: E2E-005, SERVER-005
- Blocks: —

## Spec References
- specs.md Section 10.3 — "WebSocket Protocol" (reconnection, event replay)

## Summary
E2E tests for WebSocket reconnection: verifying the UI recovers when the WebSocket connection drops, missed events are replayed, and the UI catches up to the current state.

## Acceptance Criteria
- [ ] `tests/e2e/test_websocket_reconnect.py` exists with 1-2 test functions
- [ ] Tests verify UI recovers after WebSocket disconnect
- [ ] Tests verify missed events are replayed (UI state catches up)
- [ ] All tests pass: `uv run pytest tests/e2e/test_websocket_reconnect.py -v`

## Technical Design

### Tests

1. **test_reconnect_replays_events** — Start a flow with a gate on "work", wait for "start" to complete. Use `page.evaluate()` to close the WebSocket connection. Release the gate so "work" completes while disconnected. Wait for auto-reconnect (the useWebSocket hook has exponential backoff starting at 1s). Verify the UI shows "work" as completed (replayed via event replay on subscribe with last_event_timestamp).

### Key Technique
```python
# Force disconnect the WebSocket
page.evaluate("window.__flowstate_ws?.close()")
# The UI hook will auto-reconnect after 1s
# Release gate so events happen while disconnected
gate.set()
# Wait for UI to catch up after reconnect
expect(page.locator('[data-testid="node-work"][data-status="completed"]')).to_be_visible(timeout=15000)
```

The UI's WebSocket hook must expose the WebSocket instance on `window` for testability, or the test can intercept via `page.evaluate` to find and close all WebSocket connections.

## Testing Strategy
This test is inherently timing-sensitive. Use generous timeouts (15s) and rely on the UI's auto-reconnect mechanism. The key assertion is that after reconnect, the UI reflects the state that changed while disconnected.
