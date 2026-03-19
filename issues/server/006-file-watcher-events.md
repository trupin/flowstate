# [SERVER-006] WebSocket File Watcher Events

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-002, SERVER-005
- Blocks: none

## Spec References
- specs.md Section 10.3 — "WebSocket Protocol" (`flow.file_changed`, `flow.file_error`, `flow.file_valid` events)
- specs.md Section 10.8 — "File Watcher"
- agents/04-server.md — "WebSocket Hub" (file watcher event bridging)

## Summary
Bridge the file watcher system (SERVER-002) to the WebSocket hub (SERVER-005) so that when `.flow` files change on disk, all connected WebSocket clients receive real-time notifications. These events are broadcast to ALL connected clients (not scoped to a specific `flow_run_id`), because file changes affect the flow library which is global. The UI uses these events to live-update the sidebar flow list and show/clear error banners.

## Acceptance Criteria
- [ ] When a `.flow` file is created or modified and parses successfully, a `flow.file_valid` event is broadcast to all connected WebSocket clients:
  ```json
  {
    "type": "flow.file_valid",
    "flow_run_id": null,
    "timestamp": "<iso8601>",
    "payload": {"file_path": "/absolute/path/to/file.flow", "flow_name": "my_flow"}
  }
  ```
- [ ] When a `.flow` file is created or modified and has parse/type-check errors, a `flow.file_error` event is broadcast:
  ```json
  {
    "type": "flow.file_error",
    "flow_run_id": null,
    "timestamp": "<iso8601>",
    "payload": {"file_path": "/absolute/path/to/file.flow", "flow_name": "my_flow", "errors": ["error 1", "error 2"]}
  }
  ```
- [ ] When any `.flow` file changes (regardless of validity), a `flow.file_changed` event is broadcast first:
  ```json
  {
    "type": "flow.file_changed",
    "flow_run_id": null,
    "timestamp": "<iso8601>",
    "payload": {"file_path": "/absolute/path/to/file.flow", "flow_name": "my_flow"}
  }
  ```
- [ ] Events are sent to ALL connected clients, not just those subscribed to a specific flow run
- [ ] The `FlowRegistry` event callback (from SERVER-002) is wired to the `WebSocketHub.broadcast_global_event` method
- [ ] The flow name in the payload is the DSL flow name if parseable, or the filename stem if parse fails
- [ ] All tests pass: `uv run pytest tests/server/test_file_watcher_events.py`

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/flow_registry.py` — ensure the event callback produces the right event dicts
- `src/flowstate/server/websocket.py` — ensure `broadcast_global_event` works (already designed in SERVER-005)
- `src/flowstate/server/app.py` — wire the callback during lifespan startup
- `tests/server/test_file_watcher_events.py` — all tests

### Key Implementation Details

#### Event Callback Wiring (in `app.py` lifespan)

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    config = app.state.config

    # Initialize components
    registry = FlowRegistry(watch_dir=config.watch_dir)
    ws_hub = WebSocketHub()
    # ... other setup ...

    # Wire file watcher events to WebSocket hub
    def on_file_event(event_type: str, flow: DiscoveredFlow) -> None:
        """Bridge FlowRegistry file events to WebSocket broadcasts."""
        now = datetime.now(UTC).isoformat()
        flow_name = flow.name or flow.id  # Use DSL name if available, else filename

        # Always send file_changed first
        changed_event = {
            "type": "flow.file_changed",
            "flow_run_id": None,
            "timestamp": now,
            "payload": {
                "file_path": flow.file_path,
                "flow_name": flow_name,
            },
        }
        asyncio.create_task(ws_hub.broadcast_global_event(changed_event))

        # Then send validity status
        if event_type == "file_error":
            error_event = {
                "type": "flow.file_error",
                "flow_run_id": None,
                "timestamp": now,
                "payload": {
                    "file_path": flow.file_path,
                    "flow_name": flow_name,
                    "errors": flow.errors,
                },
            }
            asyncio.create_task(ws_hub.broadcast_global_event(error_event))
        else:
            valid_event = {
                "type": "flow.file_valid",
                "flow_run_id": None,
                "timestamp": now,
                "payload": {
                    "file_path": flow.file_path,
                    "flow_name": flow_name,
                },
            }
            asyncio.create_task(ws_hub.broadcast_global_event(valid_event))

    registry.set_event_callback(on_file_event)

    app.state.flow_registry = registry
    app.state.ws_hub = ws_hub

    await registry.start()
    yield
    await registry.stop()
```

#### Event Sequence

When a `.flow` file is modified:
1. `watchfiles` detects the change
2. `FlowRegistry._watch_loop` re-parses the file
3. `FlowRegistry` calls `self._event_callback(event_type, flow)`
4. The callback creates `flow.file_changed` + `flow.file_valid`/`flow.file_error` events
5. Events are sent via `asyncio.create_task(ws_hub.broadcast_global_event(...))`
6. All connected WebSocket clients receive both events

The `flow.file_changed` event always fires first, so the UI can show a "reloading..." state. The subsequent `flow.file_valid` or `flow.file_error` event provides the result.

#### `broadcast_global_event` (already in SERVER-005)

This method iterates over ALL connected clients (regardless of subscription) and sends the event. It is already defined in the `WebSocketHub` class from SERVER-005.

### Edge Cases
- No WebSocket clients connected: events are generated but `broadcast_global_event` has nothing to iterate — no-op, no error.
- File parse fails so badly that the flow name is unknown: use the filename stem as `flow_name` (the `DiscoveredFlow.name` will be `None`).
- Rapid file changes (editor save multiple times quickly): `watchfiles` debounces by default, but the registry processes each change. Multiple events may arrive in quick succession — the UI should handle this gracefully (overwrite previous state).
- File is deleted: no `file_changed`/`file_valid`/`file_error` event needed. The flow simply disappears from the registry. The UI can poll `GET /api/flows` to detect removal, or a future enhancement could add a `flow.file_deleted` event.
- The callback runs in the context of the `_watch_loop` asyncio task. Using `asyncio.create_task` ensures the broadcast does not block the watcher.

## Testing Strategy

Create `tests/server/test_file_watcher_events.py`:

1. **test_file_change_sends_websocket_events** — Set up a `FlowRegistry` with a valid `.flow` file in a tmp dir. Connect a WebSocket client. Modify the file. Verify the client receives `flow.file_changed` followed by `flow.file_valid`.

2. **test_file_error_sends_error_event** — Start with a valid file, modify it to introduce a parse error. Verify the client receives `flow.file_changed` followed by `flow.file_error` with the error list.

3. **test_events_broadcast_to_all_clients** — Connect two WebSocket clients (with different/no subscriptions). Modify a file. Verify BOTH clients receive the events.

4. **test_flow_name_from_dsl** — Valid file with `flow my_flow { ... }`. Verify `flow_name` in the event payload is `"my_flow"`.

5. **test_flow_name_fallback_to_filename** — Invalid file that cannot be parsed. Verify `flow_name` is the filename stem.

6. **test_no_clients_no_error** — Modify a file with no WebSocket clients connected. Verify no exception is raised.

7. **test_event_ordering** — Verify that `flow.file_changed` arrives before `flow.file_valid`/`flow.file_error` for each file change.

For tests that involve actual file watching, use `tmp_path` and allow a brief `asyncio.sleep` for the watcher to detect changes. For faster unit tests, call the `on_file_event` callback directly and mock `broadcast_global_event` to verify the events.
