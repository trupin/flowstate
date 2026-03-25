# [ENGINE-035] ACP-only agent execution + long-lived session lifecycle

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: ENGINE-036

## Spec References
- specs.md Section 9.1 — "Task Subprocess Invocation"
- specs.md Section 9.8 — "Agent Harnesses (ACP)"

## Summary
Refactor AcpHarness to support long-lived sessions where the agent subprocess survives between `prompt()` calls. Currently each `run_task()` call spawns a subprocess, sends one prompt, and cleans up. The new model separates session startup from prompting, enabling multiple prompt rounds per task (needed for user message re-invocation) and interrupt-without-kill (needed for the interrupt button). Also make ACP the sole agent harness — SubprocessManager is no longer used for task execution.

## Acceptance Criteria
- [ ] AcpHarness supports long-lived sessions: subprocess stays alive between `prompt()` calls
- [ ] New method `start_session(workspace, session_id)` spawns subprocess + initializes ACP + creates/loads session
- [ ] New method `prompt(session_id, message)` sends a prompt to an existing session and streams events (async generator)
- [ ] New method `interrupt(session_id)` cancels the current prompt via `conn.cancel()` WITHOUT killing the subprocess
- [ ] `kill(session_id)` retains current behavior: terminate subprocess entirely
- [ ] `run_task()` and `run_task_resume()` still work as convenience wrappers (start_session + prompt)
- [ ] `DEFAULT_HARNESS` changed from `"claude"` to an ACP-based harness
- [ ] SubprocessManager is no longer used as a task harness (kept only for ENGINE-037 cleanup)
- [ ] Judge evaluation uses AcpHarness.run_judge() exclusively
- [ ] All existing engine tests pass (mocks updated as needed)

## Technical Design

### Files to Modify

- `src/flowstate/engine/acp_client.py` — Major refactor. Separate the `_run_acp_session` method into `start_session()`, `prompt()`, and lifecycle management. Store active sessions in `self._sessions: dict[str, _AcpSession]` where `_AcpSession` holds the connection, process, and session_id. Add `interrupt()` that calls `conn.cancel()` without killing the subprocess.

- `src/flowstate/engine/harness.py` — Extend `Harness` protocol:
  - `async def start_session(self, workspace: str, session_id: str) -> None`
  - `async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]`
  - `async def interrupt(self, session_id: str) -> None`
  - Change `DEFAULT_HARNESS` from `"claude"` to the configured ACP harness name.
  - Update `HarnessManager` to use AcpHarness as default.

- `src/flowstate/engine/executor.py` — Update `_execute_subprocess_task` to use the new AcpHarness API:
  1. `await harness.start_session(workspace, session_id)` at task start
  2. `async for event in harness.prompt(session_id, prompt):` for each turn
  3. `await harness.kill(session_id)` at task end
  Remove all SubprocessManager-specific code paths for task execution. Keep SubprocessManager import only if needed for backward compat (handled in ENGINE-037).

- `src/flowstate/server/routes.py` — Remove SubprocessManager instantiation for task execution. The executor only needs a HarnessManager.

### Key Implementation Details

**Refactored AcpHarness session management:**
```python
class _AcpSession:
    conn: Any          # ACP connection
    process: Any       # Agent subprocess
    session_id: str    # ACP session ID
    _queue: asyncio.Queue[StreamEvent | None]
    _bridge: _AcpBridgeClient

class AcpHarness:
    _sessions: dict[str, _AcpSession] = {}

    async def start_session(self, workspace: str, session_id: str) -> None:
        # Spawn subprocess, initialize ACP, create session
        # Store in self._sessions[session_id]

    async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]:
        session = self._sessions[session_id]
        result = await session.conn.prompt([text_block(message)], session_id=session.session_id)
        # Yield queued events from bridge
        while not session._queue.empty():
            event = session._queue.get_nowait()
            if event is not None:
                yield event
        # Yield final result event

    async def interrupt(self, session_id: str) -> None:
        session = self._sessions[session_id]
        await session.conn.cancel(session_id=session.session_id)
        # Don't kill subprocess — it's still alive for re-prompting
```

**Backward-compatible convenience wrappers:**
```python
async def run_task(self, prompt, workspace, session_id, **kw):
    await self.start_session(workspace, session_id)
    async for event in self.prompt(session_id, prompt):
        yield event
    # Note: don't auto-kill — executor manages lifecycle
```

### Edge Cases
- `conn.cancel()` may fail (RequestError) → catch and log, session is still usable
- Agent subprocess crashes between prompts → detect via process.returncode, raise error on next prompt()
- Session ID collision → existing session must be killed before starting a new one
- Long idle sessions (subprocess running but no prompts) → executor should kill on task completion

## Regression Risks
- ALL engine tests that mock SubprocessManager for task execution need to mock AcpHarness instead
- The `run_task()` wrapper preserves the existing async generator API, so executor changes are minimal for this issue
- Judge tests may need updating if judge also moves to ACP
- The `_session_harness` tracking in executor may simplify since there's only one harness type

## Testing Strategy
- Unit test: start_session creates subprocess and initializes ACP
- Unit test: prompt() sends message and yields events
- Unit test: interrupt() cancels without killing subprocess
- Unit test: prompt() after interrupt() works (re-invocation)
- Unit test: kill() terminates subprocess
- Unit test: run_task() convenience wrapper works end-to-end
- Regression: all existing engine tests pass with updated mocks
- `uv run pytest tests/engine/ && uv run ruff check . && uv run pyright`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
