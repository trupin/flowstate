# [E2E-001] Mock Subprocess Manager

## Domain
e2e

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-001, ENGINE-005
- Blocks: E2E-002

## Spec References
- specs.md Section 9 — "Claude Code Integration"
- agents/03-engine.md — "Claude Code Subprocess Management"

## Summary
Create a `MockSubprocessManager` that replaces the real `SubprocessManager` during E2E tests. It returns deterministic stream-json output, supports per-node behavior configuration, controllable gates for timing-sensitive tests (pause/resume), and configurable judge decisions. This is the foundation of all E2E test determinism — without it, tests would need to call real Claude Code.

## Acceptance Criteria
- [ ] `tests/e2e/mock_subprocess.py` exists with `MockSubprocessManager`, `NodeBehavior`, `MockStreamLine`, `JudgeDecision`
- [ ] `MockSubprocessManager` implements the same async interface as `SubprocessManager`: `run_task()`, `run_task_resume()`, `run_judge()`, `kill()`
- [ ] `configure_node(node_name, behavior)` sets per-node behavior (stream lines, exit code, summary)
- [ ] `configure_judge(from_node, decision, confidence=0.9)` sets judge decisions
- [ ] `add_gate(node_name) -> threading.Event` blocks a task until `gate.set()` is called from the test thread
- [ ] `reset()` clears all configuration between tests
- [ ] `NodeBehavior.success()`, `.failure()`, `.slow()` factory methods work
- [ ] Mock writes `SUMMARY.md` to task directory when `summary_content` is set
- [ ] Gates use `threading.Event` (not `asyncio.Event`) for cross-thread sync
- [ ] Unit tests pass: `uv run pytest tests/e2e/test_mock_subprocess.py`

## Technical Design

### Files to Create
- `tests/e2e/__init__.py`
- `tests/e2e/mock_subprocess.py` — mock classes
- `tests/e2e/test_mock_subprocess.py` — unit tests for the mock itself

### Key Implementation Details

```python
from dataclasses import dataclass, field
from collections.abc import AsyncGenerator
import threading
import asyncio
import json
from pathlib import Path


@dataclass
class MockStreamLine:
    type: str  # matches StreamEventType values
    content: dict


@dataclass
class NodeBehavior:
    stream_lines: list[MockStreamLine]
    exit_code: int = 0
    summary_content: str = "Task completed successfully."
    line_delay: float = 0.01

    @staticmethod
    def success(summary: str = "Task completed successfully.") -> "NodeBehavior":
        return NodeBehavior(
            stream_lines=[
                MockStreamLine("assistant", {"type": "assistant", "content": "Working on it..."}),
                MockStreamLine("result", {"type": "result", "content": "Done."}),
            ],
            exit_code=0,
            summary_content=summary,
        )

    @staticmethod
    def failure(error_msg: str = "Task failed") -> "NodeBehavior":
        return NodeBehavior(
            stream_lines=[
                MockStreamLine("assistant", {"type": "assistant", "content": "Starting..."}),
                MockStreamLine("error", {"type": "error", "content": error_msg}),
            ],
            exit_code=1,
            summary_content="",
        )

    @staticmethod
    def slow(duration_lines: int = 20, summary: str = "Slow task done.") -> "NodeBehavior":
        lines = [
            MockStreamLine("assistant", {"type": "assistant", "content": f"Step {i}..."})
            for i in range(duration_lines)
        ]
        lines.append(MockStreamLine("result", {"type": "result", "content": "Done."}))
        return NodeBehavior(stream_lines=lines, exit_code=0, summary_content=summary, line_delay=0.05)


@dataclass
class JudgeDecision:
    target: str
    reasoning: str = "Mock decision"
    confidence: float = 0.9


class MockSubprocessManager:
    def __init__(self) -> None:
        self._behaviors: dict[str, NodeBehavior] = {}
        self._default_behavior = NodeBehavior.success()
        self._judge_decisions: dict[str, JudgeDecision] = {}
        self._gates: dict[str, threading.Event] = {}

    def configure_node(self, node_name: str, behavior: NodeBehavior) -> None: ...
    def configure_judge(self, from_node: str, decision: str, ...) -> None: ...
    def add_gate(self, node_name: str) -> threading.Event: ...
    def reset(self) -> None: ...

    async def run_task(self, prompt, workspace, session_id) -> AsyncGenerator: ...
    async def run_task_resume(self, prompt, workspace, session_id) -> AsyncGenerator: ...
    async def run_judge(self, prompt, workspace) -> "JudgeResult": ...
    async def kill(self, session_id) -> None: ...
```

The mock extracts the node name from the prompt (the engine's context.py injects identifiable markers). For gates, use `await asyncio.to_thread(gate.wait)` inside the async method to bridge threading.Event into asyncio.

### Edge Cases
- Node name not configured: use `_default_behavior` (success)
- Judge decision not configured for a node: raise an error (test must configure all conditional paths)
- Gate already set before task starts: task proceeds immediately
- Multiple tasks with same node name (cycles): use the same behavior each time unless reconfigured

## Testing Strategy
1. Test `NodeBehavior.success()` — verify stream_lines and exit_code
2. Test `NodeBehavior.failure()` — verify exit_code is 1
3. Test `configure_node` + `run_task` — yields configured stream lines
4. Test `configure_judge` + `run_judge` — returns configured decision
5. Test `add_gate` — task blocks until gate.set(), verify with asyncio
6. Test `reset` — clears all config, reverts to default behavior
7. Test unconfigured node — uses default success behavior
