# [ENGINE-058] Implement SandboxManager for OpenShell lifecycle

## Domain
engine

## Status
in_progress

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: ENGINE-059

## Spec References
- specs.md Section 3.3 — "Flow Declaration" (sandbox attribute description)

## Summary
Create a `SandboxManager` class that encapsulates all OpenShell CLI interactions behind a clean async interface. It wraps harness commands with `openshell sandbox create`, tracks active sandboxes for cleanup, and handles sandbox destruction on task completion or abort. This is a standalone utility module with no DSL dependencies.

## Acceptance Criteria
- [ ] `SandboxManager.sandbox_name()` generates deterministic names from task execution IDs
- [ ] `SandboxManager.wrap_command()` transforms `["claude"]` into `["openshell", "sandbox", "create", "--name", "<id>", "--", "claude"]`
- [ ] `wrap_command()` includes `--policy <path>` when a sandbox policy path is provided
- [ ] `register()` tracks active sandbox names
- [ ] `destroy()` calls `openshell sandbox delete <name>` and removes from tracking
- [ ] `destroy_all()` cleans up all tracked sandboxes (for flow abort/shutdown)
- [ ] `SandboxError` exception for openshell failures
- [ ] All operations are async-safe with proper locking

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/sandbox.py` — new file, SandboxManager class
- `tests/engine/test_sandbox.py` — new file, unit tests

### Key Implementation Details

**`src/flowstate/engine/sandbox.py`:**

```python
@dataclass
class SandboxManager:
    _active_sandboxes: set[str] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def sandbox_name(self, task_execution_id: str) -> str:
        return f"fs-{task_execution_id[:12]}"

    def wrap_command(
        self,
        command: list[str],
        task_execution_id: str,
        sandbox_policy: str | None = None,
    ) -> list[str]:
        name = self.sandbox_name(task_execution_id)
        wrapped = ["openshell", "sandbox", "create", "--name", name]
        if sandbox_policy:
            wrapped.extend(["--policy", sandbox_policy])
        wrapped.append("--")
        wrapped.extend(command)
        return wrapped

    async def register(self, task_execution_id: str) -> None:
        async with self._lock:
            self._active_sandboxes.add(self.sandbox_name(task_execution_id))

    async def destroy(self, task_execution_id: str) -> None:
        name = self.sandbox_name(task_execution_id)
        async with self._lock:
            self._active_sandboxes.discard(name)
        proc = await asyncio.create_subprocess_exec(
            "openshell", "sandbox", "delete", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()  # best-effort

    async def destroy_all(self) -> None:
        async with self._lock:
            names = list(self._active_sandboxes)
            self._active_sandboxes.clear()
        for name in names:
            proc = await asyncio.create_subprocess_exec(
                "openshell", "sandbox", "delete", name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
```

Use `@dataclass` (not frozen — needs mutable set and lock). Use `asyncio.Lock` for thread safety. `destroy()` and `destroy_all()` are best-effort — they log but don't raise on failure, since cleanup failure shouldn't block the flow.

### Edge Cases
- `destroy()` called for a sandbox not in the active set → no-op (discard is safe)
- `destroy_all()` called with empty set → no-op
- `openshell sandbox delete` fails (sandbox already gone) → log warning, don't raise
- Concurrent `register()`/`destroy()` calls → `asyncio.Lock` serializes access

## Testing Strategy
- Unit tests with mocked `asyncio.create_subprocess_exec`:
  - `test_sandbox_name_deterministic` — same ID always produces same name
  - `test_sandbox_name_prefix` — name starts with `fs-`
  - `test_wrap_command_basic` — verify command transformation
  - `test_wrap_command_with_policy` — verify `--policy` flag included
  - `test_wrap_command_preserves_args` — multi-arg commands wrapped correctly
  - `test_register_tracks_sandbox` — verify active set
  - `test_destroy_removes_from_set` — verify cleanup
  - `test_destroy_calls_openshell_delete` — verify subprocess invoked
  - `test_destroy_all_clears_all` — verify bulk cleanup

## E2E Verification Plan

### Verification Steps
1. Import `SandboxManager` and verify it instantiates
2. Call `wrap_command(["claude"], "abc123def456")` and verify output
3. (If openshell installed) Call `destroy("nonexistent")` — should not raise

## E2E Verification Log

### Post-Implementation Verification

**Date**: 2026-03-27

**1. Import and instantiation test:**
```
$ uv run python -c "from flowstate.engine.sandbox import SandboxManager, SandboxError; m = SandboxManager(); print(m)"
SandboxManager(_active_sandboxes=set(), _lock=<unlocked>)
```
Conclusion: Module imports cleanly and instantiates without error.

**2. wrap_command verification:**
```
$ uv run python -c "from flowstate.engine.sandbox import SandboxManager; m = SandboxManager(); print(m.wrap_command(['claude'], 'abc123def456'))"
['openshell', 'sandbox', 'create', '--name', 'fs-abc123def456', '--', 'claude']
```
Conclusion: Command wrapping produces the expected output per spec.

**3. Unit tests (23 tests, all passing):**
```
$ uv run pytest tests/engine/test_sandbox.py -v
23 passed in 0.03s
```
Tests cover: sandbox_name determinism/prefix/truncation, wrap_command basic/policy/args,
register tracking/idempotency, destroy removal/subprocess-call/unregistered/failure-handling,
destroy_all clear/empty/partial-failure, SandboxError import/raise, concurrency safety.

**4. Full engine regression suite:**
```
$ uv run pytest tests/engine/ -v
568 passed in 31.98s
```
Conclusion: No regressions in existing engine tests.

**5. Lint and type checks:**
```
$ uv run ruff check src/flowstate/engine/sandbox.py tests/engine/test_sandbox.py
All checks passed!

$ uv run pyright src/flowstate/engine/sandbox.py
0 errors, 0 warnings, 0 informations
```
Conclusion: Clean lint and type check.

## Completion Checklist
- [x] Unit tests written and passing
- [x] `/simplify` run on all changed code
- [x] `/lint` passes (ruff, pyright, eslint)
- [x] Acceptance criteria verified
- [x] E2E verification log filled in with concrete evidence
