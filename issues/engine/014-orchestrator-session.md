# [ENGINE-014] Orchestrator Session Manager

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-013
- Blocks: ENGINE-015, ENGINE-016

## Spec References
- specs.md Section 9 — "Claude Code Integration"
- specs.md Section 5.4 — "Execution Engine"

## Summary
Create `OrchestratorManager` — a class that tracks long-lived Claude Code sessions per `(harness, cwd)` within a flow run. Instead of spawning a new process per task, the orchestrator session is resumed for each action (task execution or judge evaluation). The first call creates a fresh session with the system prompt; subsequent calls return the existing session for resume.

## Acceptance Criteria
- [ ] `OrchestratorSession` dataclass with: session_id, harness, cwd, data_dir, is_initialized
- [ ] `OrchestratorManager` class with `get_or_create()`, `terminate()`, `terminate_all()`
- [ ] `get_or_create(harness, cwd, flow, run_id, run_data_dir)` returns OrchestratorSession
- [ ] First call creates session via subprocess_mgr.run_task() with orchestrator system prompt and --model sonnet
- [ ] Subsequent calls return the cached session (for resume via run_task_resume)
- [ ] Session metadata persisted to `~/.flowstate/runs/<run-id>/orchestrator/<cwd-hash>/session_id`
- [ ] `terminate(session_id)` removes session from tracking
- [ ] `terminate_all(run_id)` cleans up all orchestrator sessions for a run
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/orchestrator.py` — New file: OrchestratorManager, OrchestratorSession
- `tests/engine/test_orchestrator.py` — Tests

### Key Implementation Details

#### OrchestratorSession

```python
@dataclass
class OrchestratorSession:
    session_id: str
    harness: str
    cwd: str
    data_dir: str
    is_initialized: bool = False
```

#### OrchestratorManager

```python
class OrchestratorManager:
    def __init__(self, subprocess_mgr: SubprocessManager) -> None:
        self._subprocess_mgr = subprocess_mgr
        self._sessions: dict[str, OrchestratorSession] = {}  # key: "<harness>-<cwd_hash>"

    async def get_or_create(
        self, harness: str, cwd: str, flow: Flow, run_id: str, run_data_dir: str
    ) -> OrchestratorSession:
        """Get existing orchestrator session or create a new one."""
        key = self._session_key(harness, cwd)
        if key in self._sessions:
            return self._sessions[key]
        # Create new session
        session = await self._create_session(harness, cwd, flow, run_id, run_data_dir)
        self._sessions[key] = session
        return session

    async def terminate_all(self, run_id: str) -> None:
        """Terminate all orchestrator sessions for a run."""
```

#### Session Persistence

Write `session_id` to `~/.flowstate/runs/<run-id>/orchestrator/<cwd-hash>/session_id` so that:
- On engine restart, existing sessions can be discovered and resumed
- The session_id file serves as a marker that an orchestrator exists for this cwd

### Edge Cases
- Engine crashes mid-initialization — session_id file not written, next start creates fresh
- Multiple cwds in one flow — one orchestrator per cwd, each tracked independently
- Orchestrator process exits unexpectedly — SubprocessManager detects via process exit event
- Concurrent get_or_create calls for same key — use asyncio.Lock to prevent races

## Testing Strategy
1. **test_get_or_create_new** — First call creates session, verify session_id assigned
2. **test_get_or_create_cached** — Second call returns same session
3. **test_different_cwds** — Two different cwds create two sessions
4. **test_terminate** — Terminate session, verify removed from tracking
5. **test_terminate_all** — Multiple sessions, terminate all, verify all cleaned up
6. **test_session_persistence** — Verify session_id file written to disk
