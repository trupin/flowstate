# [ENGINE-032] Atomic node — exclusive execution mutex

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
Implement atomic node execution: only one task can execute an atomic node at a time across all concurrent runs of the same flow.

## Acceptance Criteria
- [ ] Atomic nodes allow only one concurrent execution per (flow_name, node_name)
- [ ] Other tasks queue and wait when the atomic is occupied
- [ ] When the running task completes, the next waiting task proceeds
- [ ] Atomic nodes have prompts (they invoke Claude Code, just exclusively)

## Technical Design
- `src/flowstate/engine/executor.py` — atomic mutex logic
- `src/flowstate/state/repository.py` — query for running atomic tasks

## Testing Strategy
- `uv run pytest tests/engine/ -v`
