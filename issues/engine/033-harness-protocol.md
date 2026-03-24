# [ENGINE-033] Harness Protocol + HarnessManager

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: DSL-011
- Blocks: ENGINE-034, ENGINE-035

## Spec References
- specs.md Section 9 — "Claude Code Integration"

## Summary
Introduce a `Harness` Protocol (structural typing) that formalizes the 4-method interface already used by `SubprocessManager`, `SDKRunner`, and `MockSubprocessManager`. Create a `HarnessManager` registry that resolves harness names to instances. Refactor `FlowExecutor` to resolve per-node harnesses instead of using a single `subprocess_mgr`.

## Acceptance Criteria
- [ ] `Harness` Protocol defined with `run_task`, `run_task_resume`, `run_judge`, `kill`
- [ ] `HarnessManager` maps names to `Harness` instances; `"claude"` always available
- [ ] `HarnessManager.get("unknown")` raises `HarnessNotFoundError`
- [ ] `FlowExecutor` accepts optional `harness_mgr` parameter; wraps `subprocess_mgr` if not provided
- [ ] Executor resolves harness per-node: `node.harness or flow.harness`
- [ ] Executor tracks `{session_id: harness_name}` for `kill()` dispatch
- [ ] `JudgeProtocol` type annotation changed from `SubprocessManager` to `Harness`
- [ ] All existing tests pass unchanged (backward compat via optional parameter)
- [ ] `SubprocessManager`, `SDKRunner`, and `MockSubprocessManager` all satisfy `Harness` Protocol

## Technical Design

### Files to Create
- `src/flowstate/engine/harness.py` — `Harness` Protocol, `HarnessConfig`, `HarnessManager`, `HarnessNotFoundError`

### Files to Modify
- `src/flowstate/engine/executor.py` — Add optional `harness_mgr` param; resolve per-node; track session→harness
- `src/flowstate/engine/judge.py` — Change `SubprocessManager` type annotation to `Harness`
- `src/flowstate/engine/queue_manager.py` — Accept and pass `harness_mgr`

### Key Implementation Details
```python
class Harness(Protocol):
    async def run_task(self, prompt: str, workspace: str, session_id: str, *, skip_permissions: bool = False) -> AsyncGenerator[StreamEvent, None]: ...
    async def run_task_resume(self, prompt: str, workspace: str, resume_session_id: str, *, skip_permissions: bool = False) -> AsyncGenerator[StreamEvent, None]: ...
    async def run_judge(self, prompt: str, workspace: str, *, skip_permissions: bool = False) -> JudgeResult: ...
    async def kill(self, session_id: str) -> None: ...

class HarnessManager:
    def __init__(self, default_harness: Harness, configs: dict[str, HarnessConfig] | None = None): ...
    def get(self, name: str) -> Harness: ...
```

Executor backward compat: `if harness_mgr is None: harness_mgr = HarnessManager(default_harness=subprocess_mgr)`

### Edge Cases
- `harness_mgr=None` (all existing callers) → wraps subprocess_mgr as "claude" → zero behavior change
- Node references unknown harness → `HarnessNotFoundError` at dispatch time (fail-fast)

## Testing Strategy
- `tests/engine/test_harness.py` — Test `HarnessManager.get()`, `HarnessNotFoundError`, Protocol satisfaction
- All existing engine tests pass unchanged
- `uv run pytest tests/engine/ -x`

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
