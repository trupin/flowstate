# [ENGINE-085] EventType count test pins stale value (19) — actual is 21

## Domain
engine

## Status
done

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 10.3 — Event types

## Summary
`tests/engine/test_events.py::TestEventTypeCount::test_event_type_count` hard-codes `assert len(EventType) == 19`, but the live enum has 21 entries (4 flow + 7 task + 1 edge + 2 fork + 2 judge + 2 task wait + 2 schedule + 1 subtask). The class docstring on `EventType` already says "18 engine event types plus 2 scheduling event types plus 1 subtask = 21 total", so the source is right and the test is wrong. While here, also add value-tests for the four EventType members that have no per-event coverage (`TASK_INTERRUPTED`, `TASK_RETRIED`, `TASK_SKIPPED`, `SUBTASK_UPDATED`) and complete the events.py module-docstring table.

## Acceptance Criteria
- [x] `uv run pytest tests/engine/test_events.py` passes.
- [x] `len(EventType)` equals the count asserted in the test.
- [x] All 21 `EventType` members have a corresponding `test_*` value-equality test in `TestEventTypeValues`.
- [x] `events.py` module-docstring table lists every member.

## Technical Design

### Files to Modify
- `tests/engine/test_events.py` — update the count assertion + comment, add 4 missing per-event tests.
- `src/flowstate/engine/events.py` — extend the module-docstring table with the missing rows (`task.interrupted`, `task.retried`, `task.skipped`, `subtask.updated`) so the documentation matches the enum.

### Key Implementation Details
Don't bake the breakdown into a brittle integer check that breaks on every legitimate addition — but the existing test does serve as a guard against accidental rename/duplication, so keep it. Just sync it to reality and align the comment with the class docstring.

### Edge Cases
None.

## Testing Strategy
- `uv run pytest tests/engine/test_events.py -q` passes (was 1 failed / 18 passed; now expected 22 passed).

## E2E Verification Plan

### Reproduction Steps
1. `cd /Users/theophanerupin/code/flowstate`
2. `uv run pytest tests/engine/test_events.py::TestEventTypeCount -q`
3. Expected: pass
4. Actual: `assert 21 == 19` — fails.

### Verification Steps
1. `uv run pytest tests/engine/test_events.py -q` — expect all green after the fix.

## E2E Verification Log

### Reproduction
```
$ uv run pytest tests/engine/test_events.py::TestEventTypeCount -q
F                                                                        [100%]
=================================== FAILURES ===================================
________________ TestEventTypeCount.test_event_type_count ______________________
    def test_event_type_count(self) -> None:
>       assert len(EventType) == 19
E       assert 21 == 19
```

### Post-Implementation Verification
```
$ uv run pytest tests/engine/test_events.py -q
......................                                                   [100%]
22 passed
```

## Completion Checklist
- [x] Unit tests passing
- [x] `/lint` passes
- [x] Acceptance criteria verified
