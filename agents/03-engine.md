# Agent 3: Execution Engine + Claude Code Integration

## Role

You are implementing the core orchestration engine for Flowstate: the main execution loop, Claude Code subprocess management, the judge protocol, budget enforcement, and context assembly between tasks.

Read `specs.md` sections **6 (Execution Model)**, **7 (Judge Protocol)**, and **9 (Claude Code Integration)** for the full requirements.

## Phase

**Phase 2** — depends on Agent 1 (AST + parser) and Agent 2 (state/repository). These must be complete or you must work against their documented interfaces.

## Files to Create

```
src/flowstate/engine/__init__.py
src/flowstate/engine/executor.py         ← main orchestration loop (Section 6.3)
src/flowstate/engine/subprocess_mgr.py   ← Claude Code subprocess lifecycle
src/flowstate/engine/judge.py            ← judge protocol (Section 7)
src/flowstate/engine/budget.py           ← budget guard (Section 5.6)
src/flowstate/engine/context.py          ← context assembly (handoff/session/none) + SUMMARY.md + task dirs
src/flowstate/engine/events.py           ← event types emitted to WebSocket
tests/engine/__init__.py
tests/engine/test_executor.py
tests/engine/test_judge.py
tests/engine/test_budget.py
```

## Dependencies

- **Python packages:** Standard library (`asyncio`, `subprocess`, `json`, `time`)
- **Internal:**
  - `flowstate.dsl.ast` — `Flow`, `Node`, `Edge`, `EdgeType`, `ContextMode`, `ErrorPolicy`, etc.
  - `flowstate.state.repository` — `FlowstateDB` for all state persistence

## Exported Interface

```python
from flowstate.engine.executor import FlowExecutor
from flowstate.engine.events import FlowEvent, EventType

class FlowExecutor:
    def __init__(self, db: FlowstateDB, event_callback: Callable[[FlowEvent], None],
                 max_concurrent: int = 4):
        ...

    async def execute(self, flow: Flow, params: dict[str, str | float | bool],
                      workspace: str) -> str:
        """Execute a flow. Returns the flow_run_id. Emits events via callback."""
        ...

    async def pause(self, flow_run_id: str) -> None:
        """Pause after current task(s) complete."""
        ...

    async def resume(self, flow_run_id: str) -> None:
        """Resume a paused flow."""
        ...

    async def cancel(self, flow_run_id: str) -> None:
        """Cancel the flow. Kill running subprocesses."""
        ...

    async def retry_task(self, flow_run_id: str, task_execution_id: str) -> None:
        """Retry a failed task."""
        ...

    async def skip_task(self, flow_run_id: str, task_execution_id: str) -> None:
        """Skip a failed task and continue."""
        ...
```

### Event Types

```python
@dataclass
class FlowEvent:
    type: EventType
    flow_run_id: str
    timestamp: str
    payload: dict

class EventType(Enum):
    FLOW_STARTED = "flow.started"
    FLOW_STATUS_CHANGED = "flow.status_changed"
    FLOW_COMPLETED = "flow.completed"
    FLOW_BUDGET_WARNING = "flow.budget_warning"
    TASK_STARTED = "task.started"
    TASK_LOG = "task.log"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    EDGE_TRANSITION = "edge.transition"
    FORK_STARTED = "fork.started"
    FORK_JOINED = "fork.joined"
    JUDGE_STARTED = "judge.started"
    JUDGE_DECIDED = "judge.decided"
```

The `event_callback` is called for every event. The web server's WebSocket hub subscribes to these events and broadcasts them to connected clients.

## Execution Algorithm

Implement the algorithm from Section 6.3 of specs.md. The pseudocode is provided there. Key responsibilities:

### Task Scheduling
- Maintain a set of pending tasks
- A task is "ready" when all its dependencies are met:
  - Unconditional edge: predecessor completed
  - Join edge: ALL fork group members completed
- Respect concurrency semaphore (max_concurrent)

### Fork-Join Coordination
- When a fork edge is encountered: create pending tasks for ALL targets, create a fork_group record
- When a forked task completes: check if all fork group members are completed
- When all members complete: create pending task for the join target, mark fork group as "joined"
- Generation tracking: all tasks in a fork group share the same generation

### Conditional Branching
- When a node with conditional outgoing edges completes: invoke the judge
- Judge returns: target node name + reasoning + confidence
- If confidence < 0.5: pause flow (emit event, let user decide)
- If decision is "__none__": pause flow
- Otherwise: create pending task for the chosen target

### Cycle Re-entry
- When a conditional edge targets an already-executed node: increment generation
- Create a NEW task_execution (new session, not resumed)
- The context on the edge determines what context the re-entered task receives

### Budget Enforcement
- Track elapsed time across all tasks (sum of task elapsed_seconds)
- After each task completion: check if elapsed >= budget_seconds
- Emit warnings at 75%, 90%, 95%
- On exceeded: complete current task, then pause flow

### Error Handling
- Apply the flow's `on_error` policy (Section 3.2):
  - `pause`: pause flow, emit event
  - `abort`: cancel flow, kill running subprocesses
  - `skip`: mark task as skipped, continue via first outgoing edge

## Claude Code Subprocess Management (`subprocess_mgr.py`)

### Launching a task (handoff/none mode — fresh session)

```python
async def run_task(self, prompt: str, workspace: str,
                   session_id: str) -> AsyncGenerator[StreamEvent, None]:
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Read stdout line by line, parse JSON, yield StreamEvent objects
    # On process exit: yield completion event with exit code
```

### Launching a task (session mode — resume previous session)

```python
async def run_task_resume(self, prompt: str, workspace: str,
                          resume_session_id: str) -> AsyncGenerator[StreamEvent, None]:
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--resume", resume_session_id,
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Same output handling as fresh session
```

### Launching a judge

```python
async def run_judge(self, prompt: str, workspace: str) -> JudgeDecision:
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt,
        "--output-format", "json",
        "--permission-mode", "plan",
        "--model", "sonnet",
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Read full stdout, parse JSON, extract decision
    # Return JudgeDecision(target, reasoning, confidence)
```

### Output Parsing

With `--output-format stream-json`, Claude Code outputs one JSON object per line. Parse each line and categorize:
- `type: "assistant"` → assistant message (log)
- `type: "tool_use"` → tool invocation (log)
- `type: "tool_result"` → tool output (log)
- `type: "result"` → final result (task completion)
- `type: "error"` → error (task failure)

Store each event in `task_logs` table AND emit as a `FlowEvent`.

## Judge Protocol (`judge.py`)

Implement Section 7 of specs.md:

1. Build the judge prompt from the template (Section 7.1)
2. Build the JSON schema with the correct enum values (target node names + "__none__")
3. Invoke via `subprocess_mgr.run_judge()`
4. Parse the response
5. Handle failures per Section 7.4 (retry once, then pause)

## Budget Guard (`budget.py`)

```python
class BudgetGuard:
    def __init__(self, budget_seconds: int):
        self.budget_seconds = budget_seconds
        self.elapsed = 0.0
        self._warned = set()  # thresholds already warned about

    def add_elapsed(self, seconds: float) -> list[str]:
        """Add task elapsed time. Returns list of threshold warnings crossed."""
        self.elapsed += seconds
        warnings = []
        for threshold in [0.75, 0.90, 0.95]:
            if threshold not in self._warned and self.elapsed >= self.budget_seconds * threshold:
                self._warned.add(threshold)
                warnings.append(f"{int(threshold * 100)}%")
        return warnings

    @property
    def exceeded(self) -> bool:
        return self.elapsed >= self.budget_seconds
```

## Context Assembly (`context.py`)

Handles task directory setup, SUMMARY.md management, and prompt construction based on context mode.

### Task directory lifecycle
1. Before launching a task: create `.flowstate/tasks/<name>-<gen>/`
2. If `.flowstate/` doesn't exist: create it with a `.gitignore` containing `*`
3. After task completes: verify `SUMMARY.md` exists in the task directory. If missing, log a warning.

### Prompt construction by context mode

| Mode | What to include in the prompt |
|------|-------------------------------|
| `handoff` | Read `SUMMARY.md` from the predecessor task's directory. Inject it as "Context from previous task" section. Start a fresh Claude Code session. |
| `session` | No context section needed — use `--resume <session_id>` to continue the predecessor's session. Only send the new task prompt as a follow-up. |
| `none` | No upstream context. Just the task's own prompt. Fresh session. |

### Join context aggregation
For join edges (always `handoff` — `session` not allowed on joins):
- Read `SUMMARY.md` from each fork member's task directory
- Aggregate into the join task's prompt with headers per member

### SUMMARY.md instruction
Every task prompt (regardless of mode) must include an instruction to write `SUMMARY.md`:
```
When you are done, write a SUMMARY.md to .flowstate/tasks/<name>-<gen>/
describing: what you did, what changed, and the outcome.
```

### Resolving effective context mode
```python
def get_context_mode(edge: Edge, flow: Flow) -> ContextMode:
    """Edge-level override takes precedence over flow-level default."""
    if edge.config.context is not None:
        return edge.config.context
    return flow.context
```

## Testing Requirements

### `test_executor.py`
- **Mock the subprocess manager** — don't actually call Claude Code in tests
- Test linear flow execution (3 nodes, 2 unconditional edges)
- Test fork-join execution (fork into 2, join, verify both ran)
- Test conditional branching (mock judge returning each possible decision)
- Test cycle execution (mock judge returning "needs work" twice then "approved")
- Test budget exceeded (mock tasks with known durations, verify pause at threshold)
- Test on_error policies: pause, abort, skip
- Test concurrent task limit (fork into 5 with max_concurrent=2, verify max 2 run simultaneously)

### `test_judge.py`
- Test prompt construction from template
- Test JSON schema generation with correct enum values
- Test happy path: judge returns valid decision
- Test retry on malformed output
- Test pause on low confidence
- Test pause on "__none__" decision

### `test_budget.py`
- Test threshold warnings at 75%, 90%, 95%
- Test exceeded detection
- Test that warnings are not repeated

## Key Constraints

1. **All I/O is async.** Use `asyncio` for subprocess management and coordination.
2. **Events are the ONLY way the engine communicates with the outside world** (web server, UI). Every significant state change must emit an event.
3. **The engine must be pausable and resumable.** Pausing means: let current tasks finish, don't start new ones. Resuming means: pick up from where we left off.
4. **Do not import from `flowstate.server`.** The engine knows nothing about HTTP or WebSocket. It just emits events.
5. **Use `pytest` + `pytest-asyncio` for all tests.**
6. **Verify exact Claude Code CLI flags exist** before using them. The spec lists flags that should work but verify against `claude --help` at implementation time.
