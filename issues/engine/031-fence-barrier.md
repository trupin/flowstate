# [ENGINE-031] Fence node — synchronization barrier

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: DSL-009
- Blocks: —

## Summary
Implement fence node execution: all task executions in a flow run must arrive at the fence before any can proceed past it.

## Acceptance Criteria
- [ ] Fence node marks arriving tasks as waiting
- [ ] When all tasks reach the fence, all are released to continue
- [ ] Fence works with fork-join parallel execution
- [ ] Fence has no prompt (no Claude Code invocation)

## Technical Design
- `src/flowstate/engine/executor.py` — fence barrier logic

## Testing Strategy
- `uv run pytest tests/engine/ -v`
