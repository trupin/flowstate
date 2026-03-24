# [ENGINE-034] ACP harness implementation

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-033
- Blocks: —

## Spec References
- specs.md Section 9 — "Claude Code Integration"
- https://agentclientprotocol.com — ACP protocol spec

## Summary
Implement `AcpHarness` — a generic ACP client that can connect to any ACP-compatible agent subprocess and translate between ACP's JSON-RPC protocol and Flowstate's `StreamEvent` model. Uses the `agent-client-protocol` Python SDK.

## Acceptance Criteria
- [ ] `AcpHarness` satisfies the `Harness` Protocol
- [ ] `run_task`: spawns agent, initializes ACP, creates session, sends prompt, streams events
- [ ] `run_task_resume`: uses `session/load` if supported, falls back to fresh session
- [ ] `run_judge`: collects response text, parses as JSON for `JudgeResult`
- [ ] `kill`: sends `session/cancel`, terminates subprocess
- [ ] ACP `session/update` notifications mapped to `StreamEvent` types correctly
- [ ] Handles agent crash (EOF on stdio) → emits SYSTEM exit event
- [ ] `agent-client-protocol` added to `pyproject.toml` as optional dependency
- [ ] SDK imported lazily (no import-time cost when ACP harnesses aren't used)

## Technical Design

### Files to Create
- `src/flowstate/engine/acp_client.py` — `AcpHarness` class

### Files to Modify
- `pyproject.toml` — Add `agent-client-protocol>=0.8` dependency
- `src/flowstate/engine/harness.py` — Import `AcpHarness` lazily in `HarnessManager.get()` for non-claude names

### Key Implementation Details

Bridge ACP's callback model to AsyncGenerator via `asyncio.Queue`:
```python
class _AcpBridgeClient(Client):
    def __init__(self, queue: asyncio.Queue[StreamEvent | None]):
        self._queue = queue

    async def session_update(self, session_id, update, **kwargs):
        event = _map_acp_update_to_stream_event(update)
        if event:
            self._queue.put_nowait(event)

    async def request_permission(self, options, session_id, tool_call, **kwargs):
        return {"outcome": {"outcome": "allowed"}}  # auto-approve
```

ACP → StreamEvent mapping:
| ACP update | StreamEventType |
|---|---|
| `agent_message_chunk` (text) | ASSISTANT |
| `agent_message_chunk` (thinking) | ASSISTANT |
| `tool_call` | TOOL_USE |
| `tool_call_update` (completed) | TOOL_RESULT |
| `plan` | SYSTEM |
| Response `end_turn` | RESULT + SYSTEM (exit_code=0) |
| Response `cancelled` | SYSTEM (exit_code=-1) |

### Edge Cases
- Agent doesn't support `session/load` → catch `RequestError.method_not_found()`, fall back to fresh session
- Agent sends unknown update types → skip (log warning)
- JSON-RPC error → emit SYSTEM error event
- Process exits unexpectedly → detect EOF, emit exit event with non-zero code

## Testing Strategy
- `tests/engine/test_acp_client.py` — Test with mock echo ACP agent script
- Test `run_task` yields correct StreamEvents
- Test `run_judge` parses JSON response
- Test `kill` sends cancel
- Test error handling: crash, malformed output
- `uv run pytest tests/engine/ -x`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
