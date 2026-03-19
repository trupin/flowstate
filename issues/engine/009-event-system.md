# [ENGINE-009] Event System

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-005
- Blocks: SERVER-005

## Spec References
- specs.md Section 10.3 — "WebSocket Protocol" (all event types and payloads)
- agents/03-engine.md — "Event Types"

## Summary
Implement the event system that enables the engine to communicate with the outside world (web server, WebSocket hub, UI). Define the `FlowEvent` dataclass and `EventType` enum covering all event types from the spec. Wire event emission into the executor at every state change point. The event system is the ONLY interface between the engine and the presentation layer — the engine never directly calls HTTP or WebSocket code. Events are emitted via a callback function provided at executor construction time, which the web server subscribes to for broadcasting.

## Acceptance Criteria
- [ ] File `src/flowstate/engine/events.py` exists and is importable
- [ ] `EventType` enum is defined with all event types from specs.md Section 10.3
- [ ] `FlowEvent` dataclass is defined with: `type: EventType`, `flow_run_id: str`, `timestamp: str`, `payload: dict`
- [ ] All 16 event types are covered:
  - `flow.started`, `flow.status_changed`, `flow.completed`, `flow.budget_warning`
  - `task.started`, `task.log`, `task.completed`, `task.failed`
  - `edge.transition`
  - `fork.started`, `fork.joined`
  - `judge.started`, `judge.decided`
  - `task.waiting`, `task.wait_elapsed`
  - (schedule events are in ENGINE-011)
- [ ] Each event type has documented payload fields matching specs.md Section 10.3
- [ ] `FlowEvent` is serializable to JSON (for WebSocket transmission)
- [ ] `to_dict()` method on `FlowEvent` returns a JSON-serializable dict
- [ ] The executor emits events at every appropriate state change (already partially done in ENGINE-005/006/007/008, but this issue ensures completeness)
- [ ] Event timestamps are ISO 8601 UTC strings
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/events.py` — event types and dataclass
- `tests/engine/test_events.py` — tests

### Key Implementation Details

#### Event Type Enum

```python
from enum import Enum


class EventType(str, Enum):
    # Flow lifecycle
    FLOW_STARTED = "flow.started"
    FLOW_STATUS_CHANGED = "flow.status_changed"
    FLOW_COMPLETED = "flow.completed"
    FLOW_BUDGET_WARNING = "flow.budget_warning"

    # Task lifecycle
    TASK_STARTED = "task.started"
    TASK_LOG = "task.log"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"

    # Edge traversal
    EDGE_TRANSITION = "edge.transition"

    # Fork-join
    FORK_STARTED = "fork.started"
    FORK_JOINED = "fork.joined"

    # Judge
    JUDGE_STARTED = "judge.started"
    JUDGE_DECIDED = "judge.decided"

    # Scheduling (edge delays)
    TASK_WAITING = "task.waiting"
    TASK_WAIT_ELAPSED = "task.wait_elapsed"

    # Recurring flow scheduling (defined here, used in ENGINE-011)
    SCHEDULE_TRIGGERED = "schedule.triggered"
    SCHEDULE_SKIPPED = "schedule.skipped"
```

#### FlowEvent Dataclass

```python
from dataclasses import dataclass, asdict
from datetime import datetime, timezone


@dataclass
class FlowEvent:
    """An event emitted by the execution engine.

    Events are the only way the engine communicates state changes
    to the web server and UI. Every significant state change must
    emit an event.
    """
    type: EventType
    flow_run_id: str
    timestamp: str  # ISO 8601 UTC
    payload: dict

    def to_dict(self) -> dict:
        """Convert to a JSON-serializable dictionary.

        The 'type' field is serialized as its string value
        (e.g., "flow.started") not the enum name.
        """
        return {
            "type": self.type.value,
            "flow_run_id": self.flow_run_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    @staticmethod
    def now() -> str:
        """Return current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()
```

#### Documented Payload Fields Per Event Type

Each event type has specific payload fields (from specs.md Section 10.3). Document these as comments or docstrings. The payload is an untyped dict for flexibility, but the documented fields are the contract:

| Event Type | Required Payload Fields |
|------------|------------------------|
| `flow.started` | `status: str`, `budget_seconds: int` |
| `flow.status_changed` | `old_status: str`, `new_status: str`, `reason: str` |
| `flow.completed` | `elapsed_seconds: float`, `final_status: str` |
| `flow.budget_warning` | `elapsed_seconds: float`, `budget_seconds: int`, `percent_used: str` |
| `task.started` | `task_execution_id: str`, `node_name: str`, `generation: int` |
| `task.log` | `task_execution_id: str`, `log_type: str`, `content: str` |
| `task.completed` | `task_execution_id: str`, `node_name: str`, `exit_code: int`, `elapsed_seconds: float` |
| `task.failed` | `task_execution_id: str`, `node_name: str`, `error_message: str` |
| `edge.transition` | `from_node: str`, `to_node: str`, `edge_type: str`, `condition: str | None`, `judge_reasoning: str | None` |
| `fork.started` | `fork_group_id: str`, `source_node: str`, `targets: list[str]` |
| `fork.joined` | `fork_group_id: str`, `join_node: str` |
| `judge.started` | `from_node: str`, `conditions: list[str]` |
| `judge.decided` | `from_node: str`, `to_node: str`, `reasoning: str`, `confidence: float` |
| `task.waiting` | `task_execution_id: str`, `node_name: str`, `wait_until: str`, `reason: str` |
| `task.wait_elapsed` | `task_execution_id: str`, `node_name: str` |
| `schedule.triggered` | `flow_definition_id: str`, `flow_run_id: str`, `cron_expression: str` |
| `schedule.skipped` | `flow_definition_id: str`, `reason: str` |

#### Helper for Event Emission

Provide a convenience function to reduce boilerplate in the executor:

```python
def make_event(
    event_type: EventType,
    flow_run_id: str,
    **payload_fields,
) -> FlowEvent:
    """Create a FlowEvent with automatic timestamp."""
    return FlowEvent(
        type=event_type,
        flow_run_id=flow_run_id,
        timestamp=FlowEvent.now(),
        payload=payload_fields,
    )
```

#### Event Emission Points in the Executor

Audit all state change points across ENGINE-005 through ENGINE-008 and ensure events are emitted:

1. **flow.started** — when flow run transitions to `running`
2. **flow.status_changed** — on every flow status transition (created->running, running->paused, paused->running, running->cancelled, etc.)
3. **flow.completed** — when exit node finishes and flow status becomes `completed`
4. **flow.budget_warning** — when BudgetGuard.add_elapsed returns threshold warnings
5. **task.started** — when task status transitions to `running`
6. **task.log** — for each StreamEvent from the subprocess (assistant, tool_use, tool_result)
7. **task.completed** — when task finishes successfully (exit code 0)
8. **task.failed** — when task fails (non-zero exit or exception)
9. **edge.transition** — when creating a new task from an outgoing edge (unconditional, conditional, fork, join)
10. **fork.started** — when fork group is created
11. **fork.joined** — when all fork members complete and join target is created
12. **judge.started** — before invoking JudgeProtocol.evaluate
13. **judge.decided** — after judge returns a decision
14. **task.waiting** — when a task is created with delayed status (ENGINE-010)
15. **task.wait_elapsed** — when a waiting task transitions to pending (ENGINE-010)

### Edge Cases
- **Event callback raises an exception**: The executor should NOT crash if the event callback fails. Wrap callback invocations in try/except and log errors.
- **Null/None payload fields**: Some events may have optional payload fields (e.g., `condition` is None for unconditional edge transitions). These should be included as None in the payload dict.
- **Event ordering**: Events should be emitted in chronological order. Since the executor is async, events from concurrent tasks may interleave. This is expected and correct — the timestamp provides ordering.
- **High-frequency task.log events**: During streaming, many log events are emitted per second. The event callback should be efficient. The engine does not batch or throttle events — that's the web server's responsibility.
- **Serialization of non-serializable types**: The `payload` dict must contain only JSON-serializable types (str, int, float, bool, list, dict, None). Do not put dataclasses, enums, or other complex objects in the payload.

## Testing Strategy

Create `tests/engine/test_events.py`:

1. **test_event_type_values** — Verify each EventType member has the correct string value (e.g., `EventType.FLOW_STARTED.value == "flow.started"`).

2. **test_event_type_count** — Verify there are exactly 17 event types (15 engine + 2 schedule).

3. **test_flow_event_creation** — Create a FlowEvent with all fields. Verify attributes.

4. **test_flow_event_to_dict** — Create a FlowEvent, call `to_dict()`. Verify the result is a plain dict with `type` as string (not enum).

5. **test_flow_event_to_dict_serializable** — Call `json.dumps(event.to_dict())` to verify it's JSON-serializable.

6. **test_make_event_helper** — Use `make_event()` to create an event. Verify type, flow_run_id, and payload fields. Verify timestamp is a valid ISO 8601 string.

7. **test_event_timestamp_format** — Verify `FlowEvent.now()` returns a valid ISO 8601 UTC timestamp.

8. **test_flow_started_payload** — Create a `flow.started` event with correct payload fields. Verify via `to_dict()`.

9. **test_task_log_payload** — Create a `task.log` event. Verify payload has `task_execution_id`, `log_type`, `content`.

10. **test_edge_transition_payload_conditional** — Create an `edge.transition` event for a conditional edge. Verify payload has `condition` and `judge_reasoning`.

11. **test_edge_transition_payload_unconditional** — Create an `edge.transition` event for an unconditional edge. Verify `condition` is None and `judge_reasoning` is None.

Additionally, add integration-style tests (in `test_executor.py`) that verify correct event sequences:

12. **test_linear_flow_event_sequence** — Run a 3-node linear flow. Capture all events. Verify the sequence: `flow.started`, `task.started`, `task.log`*, `task.completed`, `edge.transition`, `task.started`, ..., `flow.completed`.

13. **test_fork_join_event_sequence** — Run a fork-join flow. Verify: `fork.started`, `task.started` (x2), `task.completed` (x2), `fork.joined`, `task.started`, `task.completed`, `flow.completed`.

14. **test_conditional_event_sequence** — Run a conditional flow. Verify: `task.completed`, `judge.started`, `judge.decided`, `edge.transition`, `task.started`.

15. **test_event_callback_exception_handled** — Pass an event_callback that raises an exception. Verify the executor does NOT crash and continues execution.
