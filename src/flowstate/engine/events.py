"""Flow event types emitted by the execution engine.

Every significant state change in the engine is communicated via FlowEvent objects.
The web server's WebSocket hub subscribes to these events and broadcasts them to
connected clients. The engine itself never imports from the server layer.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class EventType(StrEnum):
    """All event types emitted during flow execution."""

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


@dataclass
class FlowEvent:
    """A single event emitted by the execution engine.

    Attributes:
        type: The event category.
        flow_run_id: The flow run this event belongs to.
        timestamp: ISO 8601 UTC timestamp of when the event occurred.
        payload: Arbitrary key-value data specific to the event type.
    """

    type: EventType
    flow_run_id: str
    timestamp: str
    payload: dict[str, object] = field(default_factory=dict)
