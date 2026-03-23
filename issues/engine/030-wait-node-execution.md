# [ENGINE-030] Wait node execution + per-flow max_parallel

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: DSL-009, STATE-008
- Blocks: —

## Summary
Implement wait node execution in the executor (time-based pause using DelayChecker) and per-flow max_parallel in the queue manager.

## Acceptance Criteria
- [ ] Wait nodes with delay: task enters waiting status for the duration
- [ ] Wait nodes with until (cron): task waits until next cron match
- [ ] Wait time doesn't count toward budget
- [ ] Queue manager reads max_parallel from flow AST
- [ ] Queue manager handles scheduled tasks (only process when due)
- [ ] Recurring tasks auto-create next occurrence

## Technical Design
- `src/flowstate/engine/executor.py` — handle WAIT node type
- `src/flowstate/engine/queue_manager.py` — per-flow max_parallel, scheduled task handling

## Testing Strategy
- `uv run pytest tests/engine/ -v`
