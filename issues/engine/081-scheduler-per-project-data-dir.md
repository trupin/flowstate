# [ENGINE-081] Scheduler: scheduled flow runs use per-project data dir

## Domain
engine

## Status
done

**Eval verdict: PASS (issues/evals/sprint-phase-32-eval.md, batch-level)**

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-007, ENGINE-080
- Blocks: —

## Spec References
- specs.md §13.3 Project Layout — "Per-project data directory"

## Summary
`src/flowstate/engine/scheduler.py:160` and `:188` hardcode `data_dir=f"~/.flowstate/runs/queued-{schedule.id}"` and `data_dir=f"~/.flowstate/runs/scheduled-{schedule.id}"` when creating flow_run rows for triggered schedules. This bypasses the per-project data directory contract Phase 31.1 established. Two scheduled runs from two different projects now write to the same `~/.flowstate/runs/` namespace and could collide on schedule IDs. Fix: thread `Project` into the `Scheduler` and route these paths through `project.data_dir`.

## Acceptance Criteria
- [ ] `Scheduler.__init__` accepts a `project: Project` argument (sourced from `QueueManager._project`, which already holds it).
- [ ] The two `data_dir=f"~/.flowstate/runs/..."` callsites use `str(self._project.data_dir / "runs" / "queued-{id}")` and `str(self._project.data_dir / "runs" / "scheduled-{id}")` respectively.
- [ ] No remaining `~/.flowstate/runs/` literals in `src/flowstate/`.
- [ ] `QueueManager` constructs the `Scheduler` with its `self._project`.
- [ ] New unit test in `tests/engine/test_scheduler.py` (or wherever existing tests live): two `Scheduler` instances with two distinct `Project`s trigger one schedule each; the resulting `data_dir` values are on disjoint paths under each project's `data_dir`.
- [ ] Existing scheduler tests pass after the constructor signature change (update fixtures).

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/scheduler.py` — accept `project: Project`, replace the two hardcoded strings.
- `src/flowstate/engine/queue_manager.py` — pass `self._project` when constructing `Scheduler`.
- `tests/engine/test_scheduler.py` — fixture update + new isolation test.

### Key Implementation Details
```python
class Scheduler:
    def __init__(
        self,
        db: FlowstateDB,
        project: Project,
        ...,
    ) -> None:
        self._db = db
        self._project = project
        ...

    def _trigger(self, schedule, now):
        ...
        if overlap == "queue" and has_active:
            data_dir = str(self._project.data_dir / "runs" / f"queued-{schedule.id}")
            flow_run_id = self._db.create_flow_run(
                flow_definition_id=schedule.flow_definition_id,
                data_dir=data_dir,
                ...
            )
        ...
        # parallel branch
        data_dir = str(self._project.data_dir / "runs" / f"scheduled-{schedule.id}")
        flow_run_id = self._db.create_flow_run(
            flow_definition_id=schedule.flow_definition_id,
            data_dir=data_dir,
            ...
        )
```

### Edge Cases
- `project.data_dir / "runs"` directory does not exist yet → `create_flow_run` is a DB write only; the path is a string column, not an mkdir target. If anything later writes to it, ensure idempotent mkdir on first use (probably handled by ENGINE-080's auto-workspace path which uses a different subtree).
- Tests that previously relied on the `~/.flowstate/runs/...` literal in DB rows must be updated to assert "starts with project's data_dir" instead.

## Testing Strategy
- Unit tests in `tests/engine/test_scheduler.py`:
  - Construct two `Scheduler` instances with two `make_project_fixture(tmp_path, ...)` projects. Trigger one schedule via each. Assert the two resulting `data_dir` strings are on disjoint paths and both start with the respective project's `data_dir`.
  - Single-project test: trigger a schedule, assert the `data_dir` matches `<project.data_dir>/runs/scheduled-<schedule_id>`.
- Update any existing tests that asserted on the legacy `~/.flowstate/runs/` literal.

## E2E Verification Plan

### Verification Steps
1. Two scratch projects under `/tmp/fs-sched-a/` and `/tmp/fs-sched-b/`, each with a single scheduled flow (cron `* * * * *` so it triggers immediately).
2. Start servers in each on different ports.
3. After the first scheduler tick, query the DB row for the triggered run.
4. Assert the `data_dir` column starts with `~/.flowstate/projects/<slug>/runs/scheduled-...` for each, with distinct slugs.
5. Confirm `~/.flowstate/runs/` is **not** created.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `Scheduler` takes `Project` arg
- [ ] `QueueManager` wires it
- [ ] Two callsites migrated
- [ ] Unit test for isolation passing
- [ ] No `~/.flowstate/runs/` literal remains in src/
- [ ] `/test` passes
- [ ] `/lint` passes
- [ ] E2E steps above verified
