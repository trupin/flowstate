"""Tests for FlowEvent and EventType -- the event system (ENGINE-009).

Verifies event type completeness, FlowEvent creation, serialization,
the make_event helper, and payload field documentation compliance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from flowstate.engine.events import EventType, FlowEvent, make_event


class TestEventTypeValues:
    """Verify each EventType member has the correct string value."""

    def test_flow_started(self) -> None:
        assert EventType.FLOW_STARTED.value == "flow.started"

    def test_flow_status_changed(self) -> None:
        assert EventType.FLOW_STATUS_CHANGED.value == "flow.status_changed"

    def test_flow_completed(self) -> None:
        assert EventType.FLOW_COMPLETED.value == "flow.completed"

    def test_flow_budget_warning(self) -> None:
        assert EventType.FLOW_BUDGET_WARNING.value == "flow.budget_warning"

    def test_task_started(self) -> None:
        assert EventType.TASK_STARTED.value == "task.started"

    def test_task_log(self) -> None:
        assert EventType.TASK_LOG.value == "task.log"

    def test_task_completed(self) -> None:
        assert EventType.TASK_COMPLETED.value == "task.completed"

    def test_task_failed(self) -> None:
        assert EventType.TASK_FAILED.value == "task.failed"

    def test_edge_transition(self) -> None:
        assert EventType.EDGE_TRANSITION.value == "edge.transition"

    def test_fork_started(self) -> None:
        assert EventType.FORK_STARTED.value == "fork.started"

    def test_fork_joined(self) -> None:
        assert EventType.FORK_JOINED.value == "fork.joined"

    def test_judge_started(self) -> None:
        assert EventType.JUDGE_STARTED.value == "judge.started"

    def test_judge_decided(self) -> None:
        assert EventType.JUDGE_DECIDED.value == "judge.decided"

    def test_task_waiting(self) -> None:
        assert EventType.TASK_WAITING.value == "task.waiting"

    def test_task_wait_elapsed(self) -> None:
        assert EventType.TASK_WAIT_ELAPSED.value == "task.wait_elapsed"

    def test_schedule_triggered(self) -> None:
        assert EventType.SCHEDULE_TRIGGERED.value == "schedule.triggered"

    def test_schedule_skipped(self) -> None:
        assert EventType.SCHEDULE_SKIPPED.value == "schedule.skipped"


class TestEventTypeCount:
    """Verify there are exactly 17 event types (15 engine + 2 schedule)."""

    def test_event_type_count(self) -> None:
        assert len(EventType) == 17


class TestFlowEventCreation:
    """Create a FlowEvent with all fields. Verify attributes."""

    def test_flow_event_creation(self) -> None:
        event = FlowEvent(
            type=EventType.FLOW_STARTED,
            flow_run_id="run-123",
            timestamp="2025-01-01T00:00:00+00:00",
            payload={"status": "running", "budget_seconds": 3600},
        )
        assert event.type == EventType.FLOW_STARTED
        assert event.flow_run_id == "run-123"
        assert event.timestamp == "2025-01-01T00:00:00+00:00"
        assert event.payload == {"status": "running", "budget_seconds": 3600}

    def test_flow_event_default_payload(self) -> None:
        event = FlowEvent(
            type=EventType.TASK_LOG,
            flow_run_id="run-456",
            timestamp="2025-01-01T00:00:00+00:00",
        )
        assert event.payload == {}


class TestFlowEventToDict:
    """FlowEvent.to_dict() returns a plain dict with type as string value."""

    def test_flow_event_to_dict(self) -> None:
        event = FlowEvent(
            type=EventType.FLOW_STARTED,
            flow_run_id="run-123",
            timestamp="2025-01-01T00:00:00+00:00",
            payload={"status": "running"},
        )
        d = event.to_dict()
        assert isinstance(d, dict)
        assert d["type"] == "flow.started"  # string value, not enum
        assert d["flow_run_id"] == "run-123"
        assert d["timestamp"] == "2025-01-01T00:00:00+00:00"
        assert d["payload"] == {"status": "running"}

    def test_flow_event_to_dict_serializable(self) -> None:
        """json.dumps(event.to_dict()) should not raise."""
        event = FlowEvent(
            type=EventType.TASK_COMPLETED,
            flow_run_id="run-789",
            timestamp="2025-06-15T12:30:00+00:00",
            payload={
                "task_execution_id": "task-001",
                "node_name": "build",
                "exit_code": 0,
                "elapsed_seconds": 45.2,
            },
        )
        serialized = json.dumps(event.to_dict())
        assert isinstance(serialized, str)
        # Round-trip
        parsed = json.loads(serialized)
        assert parsed["type"] == "task.completed"
        assert parsed["payload"]["exit_code"] == 0


class TestMakeEventHelper:
    """make_event() convenience function."""

    def test_make_event_helper(self) -> None:
        event = make_event(
            EventType.TASK_STARTED,
            "run-abc",
            task_execution_id="task-001",
            node_name="build",
            generation=2,
        )
        assert event.type == EventType.TASK_STARTED
        assert event.flow_run_id == "run-abc"
        assert event.payload["task_execution_id"] == "task-001"
        assert event.payload["node_name"] == "build"
        assert event.payload["generation"] == 2

    def test_make_event_timestamp_format(self) -> None:
        """Verify make_event generates a valid ISO 8601 UTC timestamp."""
        event = make_event(EventType.FLOW_STARTED, "run-test", status="running")
        # Should be parseable
        dt = datetime.fromisoformat(event.timestamp)
        assert dt.tzinfo is not None  # Has timezone info


class TestEventTimestampFormat:
    """FlowEvent.now() returns a valid ISO 8601 UTC timestamp."""

    def test_event_timestamp_format(self) -> None:
        ts = FlowEvent.now()
        dt = datetime.fromisoformat(ts)
        assert dt.tzinfo is not None
        # Should be UTC (offset 0)
        assert dt.utcoffset() == UTC.utcoffset(None)


class TestFlowStartedPayload:
    """Create a flow.started event with correct payload fields."""

    def test_flow_started_payload(self) -> None:
        event = make_event(
            EventType.FLOW_STARTED,
            "run-001",
            status="running",
            budget_seconds=3600,
        )
        d = event.to_dict()
        assert d["payload"]["status"] == "running"
        assert d["payload"]["budget_seconds"] == 3600


class TestTaskLogPayload:
    """Create a task.log event and verify payload fields."""

    def test_task_log_payload(self) -> None:
        event = make_event(
            EventType.TASK_LOG,
            "run-001",
            task_execution_id="task-001",
            log_type="assistant",
            content='{"type": "assistant", "text": "hello"}',
        )
        assert event.payload["task_execution_id"] == "task-001"
        assert event.payload["log_type"] == "assistant"
        assert event.payload["content"] == '{"type": "assistant", "text": "hello"}'


class TestEdgeTransitionPayloadConditional:
    """edge.transition for a conditional edge includes condition and judge_reasoning."""

    def test_edge_transition_payload_conditional(self) -> None:
        event = make_event(
            EventType.EDGE_TRANSITION,
            "run-001",
            from_node="review",
            to_node="implement",
            edge_type="conditional",
            condition="needs work",
            judge_reasoning="Tests are failing",
        )
        assert event.payload["condition"] == "needs work"
        assert event.payload["judge_reasoning"] == "Tests are failing"


class TestEdgeTransitionPayloadUnconditional:
    """edge.transition for unconditional edge: condition and judge_reasoning are None."""

    def test_edge_transition_payload_unconditional(self) -> None:
        event = make_event(
            EventType.EDGE_TRANSITION,
            "run-001",
            from_node="start",
            to_node="work",
            edge_type="unconditional",
            condition=None,
            judge_reasoning=None,
        )
        assert event.payload["condition"] is None
        assert event.payload["judge_reasoning"] is None
