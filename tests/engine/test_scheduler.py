"""Tests for recurring flow scheduling (ENGINE-011).

Covers:
- Scheduler start/stop lifecycle
- Cron trigger detection and processing
- on_overlap policies (skip, queue, parallel)
- Schedule advancement (next_trigger_at, last_triggered_at)
- Error isolation between schedules
- Disabled schedule handling
- Missing flow definition handling
- Invalid cron expression handling
- Per-project data_dir routing for triggered runs (ENGINE-081)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from flowstate.config import Project, build_project
from flowstate.engine.events import EventType, FlowEvent
from flowstate.engine.scheduler import FlowScheduler
from flowstate.state.repository import FlowstateDB

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> FlowstateDB:
    """Create an in-memory FlowstateDB for testing."""
    return FlowstateDB(":memory:")


def _make_project(tmp_path: Path, slug_suffix: str = "proj") -> Project:
    """Build a throwaway :class:`Project` rooted under ``tmp_path``.

    Used by scheduler tests to satisfy the ``project: Project`` constructor
    parameter introduced in ENGINE-081. ``create_dirs=True`` so that
    ``project.data_dir`` is a real directory whose ``runs/`` subtree the
    scheduler can later compose its `data_dir` strings against.
    """
    root = tmp_path / slug_suffix
    root.mkdir(parents=True, exist_ok=True)
    return build_project(root, data_dir=tmp_path / f"data-{slug_suffix}")


@pytest.fixture()
def project(tmp_path: Path) -> Project:
    """Default scheduler-test project rooted under ``tmp_path``."""
    return _make_project(tmp_path)


def _collect_events() -> tuple[list[FlowEvent], Callable[[FlowEvent], None]]:
    """Return a list to collect events and the callback function."""
    events: list[FlowEvent] = []

    def callback(event: FlowEvent) -> None:
        events.append(event)

    return events, callback


def _create_flow_def(db: FlowstateDB, name: str = "test-flow") -> str:
    """Create a flow definition and return its ID."""
    return db.create_flow_definition(name=name, source_dsl="", ast_json='{"name": "' + name + '"}')


def _create_schedule(
    db: FlowstateDB,
    flow_definition_id: str,
    cron_expression: str = "0 9 * * MON",
    on_overlap: str = "skip",
    next_trigger_at: str | None = None,
    enabled: bool = True,
) -> str:
    """Create a flow schedule and return its ID."""
    schedule_id = db.create_flow_schedule(
        flow_definition_id=flow_definition_id,
        cron_expression=cron_expression,
        on_overlap=on_overlap,
        next_trigger_at=next_trigger_at,
    )
    if not enabled:
        db.update_flow_schedule(schedule_id, enabled=0)
    return schedule_id


def _create_active_run(db: FlowstateDB, flow_definition_id: str) -> str:
    """Create an active (running) flow run and return its ID."""
    run_id = db.create_flow_run(
        flow_definition_id=flow_definition_id,
        data_dir="/tmp/active-run",
        budget_seconds=3600,
        on_error="pause",
    )
    db.update_flow_run_status(run_id, "running")
    return run_id


# ---------------------------------------------------------------------------
# Tests: Scheduler Lifecycle
# ---------------------------------------------------------------------------


class TestSchedulerLifecycle:
    async def test_starts_and_stops(self, project: Project) -> None:
        """Start the scheduler, verify it is running, then stop it."""
        db = _make_db()
        _events, callback = _collect_events()

        scheduler = FlowScheduler(
            db=db,
            project=project,
            emit=callback,
            check_interval=0.1,
        )

        await scheduler.start()
        assert scheduler.is_running

        await asyncio.sleep(0.2)
        await scheduler.stop()
        assert not scheduler.is_running

    async def test_stop_without_start(self, project: Project) -> None:
        """Stop should be safe to call even if never started."""
        db = _make_db()
        _events, callback = _collect_events()

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.stop()  # Should not raise


# ---------------------------------------------------------------------------
# Tests: Trigger Detection
# ---------------------------------------------------------------------------


class TestTriggerDetection:
    async def test_due_schedule_fires(self, project: Project) -> None:
        """Schedule with past next_trigger_at should be processed."""
        db = _make_db()
        events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _create_schedule(db, flow_def_id, cron_expression="0 9 * * MON", next_trigger_at=past_time)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(triggered_events) == 1
        assert triggered_events[0].payload["flow_definition_id"] == flow_def_id

    async def test_future_schedule_does_not_fire(self, project: Project) -> None:
        """Schedule with future next_trigger_at should NOT be processed."""
        db = _make_db()
        events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        future_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        _create_schedule(db, flow_def_id, next_trigger_at=future_time)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(triggered_events) == 0

    async def test_disabled_schedule_ignored(self, project: Project) -> None:
        """Disabled schedule should not be processed even if due."""
        db = _make_db()
        events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _create_schedule(db, flow_def_id, next_trigger_at=past_time, enabled=False)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        assert len(events) == 0


# ---------------------------------------------------------------------------
# Tests: on_overlap Policies
# ---------------------------------------------------------------------------


class TestOverlapPolicies:
    async def test_skip_with_active_run(self, project: Project) -> None:
        """on_overlap=skip with active run: no new run, schedule.skipped emitted."""
        db = _make_db()
        events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _create_schedule(
            db,
            flow_def_id,
            on_overlap="skip",
            next_trigger_at=past_time,
        )

        # Create an active run
        _create_active_run(db, flow_def_id)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        # Should emit skipped event, not triggered
        skipped_events = [e for e in events if e.type == EventType.SCHEDULE_SKIPPED]
        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(skipped_events) == 1
        assert len(triggered_events) == 0
        assert "Active run exists" in str(skipped_events[0].payload["reason"])

    async def test_skip_without_active_run(self, project: Project) -> None:
        """on_overlap=skip with no active run: new run started."""
        db = _make_db()
        events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _create_schedule(
            db,
            flow_def_id,
            on_overlap="skip",
            next_trigger_at=past_time,
        )

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(triggered_events) == 1

    async def test_queue_with_active_run(self, project: Project) -> None:
        """on_overlap=queue with active run: new run created with 'created' status."""
        db = _make_db()
        events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _create_schedule(
            db,
            flow_def_id,
            on_overlap="queue",
            next_trigger_at=past_time,
        )

        _create_active_run(db, flow_def_id)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(triggered_events) == 1
        assert triggered_events[0].payload.get("queued") is True

        # Verify a flow run was created with 'created' status
        all_runs = db.list_flow_runs()
        created_runs = [r for r in all_runs if r.status == "created"]
        assert len(created_runs) == 1

    async def test_parallel_with_active_run(self, project: Project) -> None:
        """on_overlap=parallel with active run: new run started immediately."""
        db = _make_db()
        events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _create_schedule(
            db,
            flow_def_id,
            on_overlap="parallel",
            next_trigger_at=past_time,
        )

        _create_active_run(db, flow_def_id)

        started_flow_ids: list[str] = []

        def mock_start(fd_id: str) -> str:
            run_id = db.create_flow_run(
                flow_definition_id=fd_id,
                data_dir="/tmp/parallel-run",
                budget_seconds=3600,
                on_error="pause",
            )
            db.update_flow_run_status(run_id, "running")
            started_flow_ids.append(run_id)
            return run_id

        scheduler = FlowScheduler(
            db=db,
            project=project,
            emit=callback,
            start_flow_callback=mock_start,
        )
        await scheduler.check_once()

        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(triggered_events) == 1
        assert len(started_flow_ids) == 1

    async def test_no_active_runs_starts_regardless_of_policy(self, project: Project) -> None:
        """With no active runs, the trigger fires regardless of overlap policy."""
        db = _make_db()

        for policy in ("skip", "queue", "parallel"):
            events, callback = _collect_events()

            flow_def_id = _create_flow_def(db, name=f"flow-{policy}")
            past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
            _create_schedule(
                db,
                flow_def_id,
                on_overlap=policy,
                next_trigger_at=past_time,
            )

            scheduler = FlowScheduler(db=db, project=project, emit=callback)
            await scheduler.check_once()

            triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
            assert len(triggered_events) == 1, f"Failed for policy={policy}"


# ---------------------------------------------------------------------------
# Tests: Schedule Advancement
# ---------------------------------------------------------------------------


class TestScheduleAdvancement:
    async def test_next_trigger_computed(self, project: Project) -> None:
        """After trigger, next_trigger_at is updated to the next cron match."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        schedule_id = _create_schedule(
            db,
            flow_def_id,
            cron_expression="0 9 * * MON",
            next_trigger_at=past_time,
        )

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.next_trigger_at is not None

        # Next trigger should be in the future
        next_trigger = datetime.fromisoformat(schedule.next_trigger_at)
        assert next_trigger > datetime.now(UTC)
        # Should be on a Monday at 9:00
        assert next_trigger.weekday() == 0  # Monday
        assert next_trigger.hour == 9
        assert next_trigger.minute == 0

    async def test_last_triggered_updated(self, project: Project) -> None:
        """After trigger, last_triggered_at is set to approximately now."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        schedule_id = _create_schedule(
            db,
            flow_def_id,
            cron_expression="*/5 * * * *",
            next_trigger_at=past_time,
        )

        before = datetime.now(UTC)
        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()
        after = datetime.now(UTC)

        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.last_triggered_at is not None

        last_triggered = datetime.fromisoformat(schedule.last_triggered_at)
        assert before <= last_triggered <= after

    async def test_skip_still_advances_schedule(self, project: Project) -> None:
        """Even when skipped due to overlap, next_trigger_at should advance."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        schedule_id = _create_schedule(
            db,
            flow_def_id,
            cron_expression="*/10 * * * *",
            on_overlap="skip",
            next_trigger_at=past_time,
        )

        _create_active_run(db, flow_def_id)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.next_trigger_at is not None
        next_trigger = datetime.fromisoformat(schedule.next_trigger_at)
        assert next_trigger > datetime.now(UTC)


# ---------------------------------------------------------------------------
# Tests: Error Handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_schedule_error_isolation(self, project: Project) -> None:
        """One failing schedule should not prevent processing of others."""
        db = _make_db()
        events, callback = _collect_events()

        flow_def_id_1 = _create_flow_def(db, name="flow-1")
        flow_def_id_2 = _create_flow_def(db, name="flow-2")

        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

        # First schedule has an invalid cron (will be caught by _advance_schedule)
        _create_schedule(
            db,
            flow_def_id_1,
            cron_expression="0 9 * * MON",  # valid for triggering
            next_trigger_at=past_time,
        )

        # Second schedule should process normally
        _create_schedule(
            db,
            flow_def_id_2,
            cron_expression="0 10 * * *",
            next_trigger_at=past_time,
        )

        # Make the first schedule's flow definition unreachable to cause an error
        # Actually, both have valid definitions, so let's use a callback that fails
        # for the first one
        call_count = 0

        def failing_start(fd_id: str) -> str:
            nonlocal call_count
            call_count += 1
            if fd_id == flow_def_id_1:
                raise RuntimeError("Simulated failure")
            return db.create_flow_run(
                flow_definition_id=fd_id,
                data_dir="/tmp/run",
                budget_seconds=3600,
                on_error="pause",
            )

        scheduler = FlowScheduler(
            db=db,
            project=project,
            emit=callback,
            start_flow_callback=failing_start,
        )
        await scheduler.check_once()

        # The second schedule's trigger should have been emitted despite first failing
        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(triggered_events) >= 1

    async def test_missing_flow_definition(self, project: Project) -> None:
        """Schedule referencing a non-existent flow definition: logged, no crash."""
        db = _make_db()
        events, callback = _collect_events()

        # Create a flow definition to satisfy the FK constraint for creating
        # the schedule, then we'll simulate "missing" by making get_flow_definition
        # return None. Instead, we insert a schedule row directly with a fake
        # flow_definition_id that doesn't exist (bypass FK for this test).
        # Since FK constraints are on, we create a real flow def, create the
        # schedule, then delete the schedule and re-create it manually without FK.

        # Alternative approach: create a valid schedule, then clear the
        # flow_definitions table with FK checks disabled temporarily.
        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        schedule_id = _create_schedule(
            db,
            flow_def_id,
            cron_expression="0 9 * * MON",
            next_trigger_at=past_time,
        )

        # Temporarily disable FK checks, delete the flow definition, re-enable
        db.connection.execute("PRAGMA foreign_keys=OFF")
        db.connection.execute("DELETE FROM flow_definitions WHERE id = ?", (flow_def_id,))
        db.connection.commit()
        db.connection.execute("PRAGMA foreign_keys=ON")

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        # Should not raise
        await scheduler.check_once()

        # No triggered event should be emitted
        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(triggered_events) == 0

        # Schedule should still be advanced to avoid re-triggering
        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.next_trigger_at is not None

    async def test_invalid_cron_in_advance(self, project: Project) -> None:
        """Invalid cron expression during advancement disables the schedule."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

        # Create schedule with a cron that is valid enough for get_due_schedules
        # but we'll update it to be invalid before the advancement
        schedule_id = db.create_flow_schedule(
            flow_definition_id=flow_def_id,
            cron_expression="0 9 * * MON",
            on_overlap="skip",
            next_trigger_at=past_time,
        )

        # Manually update the cron expression to be invalid
        db.update_flow_schedule(schedule_id, cron_expression="not valid cron")

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        # Should not raise -- error is caught and logged
        await scheduler.check_once()

        # The schedule should be disabled after the invalid cron error
        schedule = db.get_flow_schedule(schedule_id)
        assert schedule is not None
        assert schedule.enabled == 0


# ---------------------------------------------------------------------------
# Tests: Start Flow Callback
# ---------------------------------------------------------------------------


class TestStartFlowCallback:
    async def test_callback_called_for_new_run(self, project: Project) -> None:
        """When a schedule fires with no overlap, the start callback is called."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _create_schedule(
            db,
            flow_def_id,
            cron_expression="0 9 * * MON",
            next_trigger_at=past_time,
        )

        started_ids: list[str] = []

        def mock_start(fd_id: str) -> str:
            run_id = "mock-run-id"
            started_ids.append(fd_id)
            return run_id

        scheduler = FlowScheduler(
            db=db,
            project=project,
            emit=callback,
            start_flow_callback=mock_start,
        )
        await scheduler.check_once()

        assert started_ids == [flow_def_id]

    async def test_no_callback_creates_run_record(self, project: Project) -> None:
        """Without a start callback, the scheduler creates a run record directly."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _create_schedule(
            db,
            flow_def_id,
            cron_expression="0 9 * * MON",
            next_trigger_at=past_time,
        )

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        # A flow run should have been created
        all_runs = db.list_flow_runs()
        assert len(all_runs) == 1
        assert all_runs[0].flow_definition_id == flow_def_id


# ---------------------------------------------------------------------------
# Tests: Multiple Schedules
# ---------------------------------------------------------------------------


class TestMultipleSchedules:
    async def test_multiple_due_schedules(self, project: Project) -> None:
        """Multiple due schedules should all be processed."""
        db = _make_db()
        events, callback = _collect_events()

        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

        flow_def_id_1 = _create_flow_def(db, name="flow-a")
        flow_def_id_2 = _create_flow_def(db, name="flow-b")

        _create_schedule(db, flow_def_id_1, next_trigger_at=past_time)
        _create_schedule(db, flow_def_id_2, next_trigger_at=past_time)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(triggered_events) == 2

        triggered_flow_ids = {e.payload["flow_definition_id"] for e in triggered_events}
        assert triggered_flow_ids == {flow_def_id_1, flow_def_id_2}

    async def test_mixed_due_and_future(self, project: Project) -> None:
        """Only due schedules should fire, not future ones."""
        db = _make_db()
        events, callback = _collect_events()

        flow_def_id_1 = _create_flow_def(db, name="flow-due")
        flow_def_id_2 = _create_flow_def(db, name="flow-future")

        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        future_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

        _create_schedule(db, flow_def_id_1, next_trigger_at=past_time)
        _create_schedule(db, flow_def_id_2, next_trigger_at=future_time)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        triggered_events = [e for e in events if e.type == EventType.SCHEDULE_TRIGGERED]
        assert len(triggered_events) == 1
        assert triggered_events[0].payload["flow_definition_id"] == flow_def_id_1


# ---------------------------------------------------------------------------
# Tests: Per-project data_dir routing (ENGINE-081)
# ---------------------------------------------------------------------------


class TestProjectDataDirRouting:
    """Triggered runs route their ``data_dir`` through the owning Project.

    Before ENGINE-081 the scheduler hardcoded ``~/.flowstate/runs/...`` so
    two projects scheduling the same flow id collided in a global namespace.
    The new constructor takes a ``Project`` and writes
    ``<project.data_dir>/runs/{queued|scheduled}-<schedule_id>`` instead.
    """

    async def test_scheduled_branch_uses_project_data_dir(self, project: Project) -> None:
        """Parallel/no-active branch writes data_dir under project.data_dir."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        schedule_id = _create_schedule(db, flow_def_id, next_trigger_at=past_time)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        runs = db.list_flow_runs()
        assert len(runs) == 1
        expected = str(project.data_dir / "runs" / f"scheduled-{schedule_id}")
        assert runs[0].data_dir == expected
        assert runs[0].data_dir.startswith(str(project.data_dir))
        assert "~/.flowstate/runs/" not in runs[0].data_dir

    async def test_queued_branch_uses_project_data_dir(self, project: Project) -> None:
        """on_overlap=queue branch writes data_dir under project.data_dir."""
        db = _make_db()
        _events, callback = _collect_events()

        flow_def_id = _create_flow_def(db)
        past_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        schedule_id = _create_schedule(
            db,
            flow_def_id,
            on_overlap="queue",
            next_trigger_at=past_time,
        )
        _create_active_run(db, flow_def_id)

        scheduler = FlowScheduler(db=db, project=project, emit=callback)
        await scheduler.check_once()

        # Filter out the seeded active run; the queued one is the new 'created'.
        created_runs = [r for r in db.list_flow_runs() if r.status == "created"]
        assert len(created_runs) == 1
        expected = str(project.data_dir / "runs" / f"queued-{schedule_id}")
        assert created_runs[0].data_dir == expected
        assert created_runs[0].data_dir.startswith(str(project.data_dir))
        assert "~/.flowstate/runs/" not in created_runs[0].data_dir

    async def test_two_projects_disjoint_paths(self, tmp_path: Path) -> None:
        """Two schedulers with two distinct projects produce disjoint paths.

        This is TEST-81.4 from the Phase 32 sprint contract: scheduling
        the same flow shape in two projects must never collide on disk
        because each scheduler roots its data_dir under its own
        ``project.data_dir``.
        """
        project_a = _make_project(tmp_path, "proja")
        project_b = _make_project(tmp_path, "projb")
        assert project_a.data_dir != project_b.data_dir

        db_a = _make_db()
        db_b = _make_db()
        _events_a, cb_a = _collect_events()
        _events_b, cb_b = _collect_events()

        past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

        fd_a = _create_flow_def(db_a, name="shared-name")
        fd_b = _create_flow_def(db_b, name="shared-name")
        sched_a = _create_schedule(db_a, fd_a, next_trigger_at=past)
        sched_b = _create_schedule(db_b, fd_b, next_trigger_at=past)

        scheduler_a = FlowScheduler(db=db_a, project=project_a, emit=cb_a)
        scheduler_b = FlowScheduler(db=db_b, project=project_b, emit=cb_b)

        await scheduler_a.check_once()
        await scheduler_b.check_once()

        runs_a = db_a.list_flow_runs()
        runs_b = db_b.list_flow_runs()
        assert len(runs_a) == 1
        assert len(runs_b) == 1

        path_a = runs_a[0].data_dir
        path_b = runs_b[0].data_dir

        # Both rooted under their respective project data dirs
        assert path_a.startswith(str(project_a.data_dir))
        assert path_b.startswith(str(project_b.data_dir))

        # Both contain the canonical "runs/scheduled-" segment
        assert "runs/scheduled-" in path_a
        assert "runs/scheduled-" in path_b
        # Sanity: schedule ids actually appear (not collapsed)
        assert sched_a in path_a
        assert sched_b in path_b

        # Paths are disjoint subtrees: neither is a prefix of the other
        assert not path_a.startswith(path_b)
        assert not path_b.startswith(path_a)

        # Guard against the legacy literal escaping into either DB row
        assert "~/.flowstate/runs/" not in path_a
        assert "~/.flowstate/runs/" not in path_b
