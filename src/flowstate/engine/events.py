"""Flow event types emitted by the execution engine.

Every significant state change in the engine is communicated via FlowEvent objects.
The web server's WebSocket hub subscribes to these events and broadcasts them to
connected clients. The engine itself never imports from the server layer.

Event types and their documented payload fields (from specs.md Section 10.3):

| Event Type             | Required Payload Fields                                              |
|------------------------|----------------------------------------------------------------------|
| flow.started           | status, budget_seconds                                               |
| flow.status_changed    | old_status, new_status, reason                                       |
| flow.completed         | elapsed_seconds, final_status                                        |
| flow.budget_warning    | elapsed_seconds, budget_seconds, percent_used                        |
| task.started           | task_execution_id, node_name, generation                             |
| task.log               | task_execution_id, log_type, content                                 |
| task.completed         | task_execution_id, node_name, exit_code, elapsed_seconds             |
| task.failed            | task_execution_id, node_name, error_message                          |
| task.interrupted       | task_execution_id, node_name                                         |
| edge.transition        | from_node, to_node, edge_type, condition (None ok), judge_reasoning  |
| fork.started           | fork_group_id, source_node, targets                                  |
| fork.joined            | fork_group_id, join_node                                             |
| judge.started          | from_node, conditions                                                |
| judge.decided          | from_node, to_node, reasoning, confidence                            |
| task.waiting           | task_execution_id, node_name, wait_until, reason                     |
| task.wait_elapsed      | task_execution_id, node_name                                         |
| schedule.triggered     | flow_definition_id, flow_run_id, cron_expression                     |
| schedule.skipped       | flow_definition_id, reason                                           |
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class EventType(StrEnum):
    """All event types emitted during flow execution.

    16 engine event types plus 2 scheduling event types = 18 total.
    """

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
    TASK_INTERRUPTED = "task.interrupted"

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

    # Agent subtask lifecycle
    SUBTASK_UPDATED = "subtask.updated"


@dataclass
class FlowEvent:
    """A single event emitted by the execution engine.

    Events are the only way the engine communicates state changes
    to the web server and UI. Every significant state change must
    emit an event.

    Attributes:
        type: The event category.
        flow_run_id: The flow run this event belongs to.
        timestamp: ISO 8601 UTC timestamp of when the event occurred.
        payload: Arbitrary key-value data specific to the event type.
            Must contain only JSON-serializable types (str, int, float,
            bool, list, dict, None).
    """

    type: EventType
    flow_run_id: str
    timestamp: str
    payload: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
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
        return datetime.now(UTC).isoformat()


def make_event(
    event_type: EventType,
    flow_run_id: str,
    **payload_fields: object,
) -> FlowEvent:
    """Create a FlowEvent with automatic timestamp.

    Convenience function to reduce boilerplate in the executor.
    """
    return FlowEvent(
        type=event_type,
        flow_run_id=flow_run_id,
        timestamp=FlowEvent.now(),
        payload=payload_fields,
    )
