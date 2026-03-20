# [ENGINE-023] Implement self-report routing (read DECISION.json from task)

## Domain
engine

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: DSL-007, ENGINE-020
- Blocks: none

## Summary
When `judge = false` (default), the task agent writes DECISION.json itself instead of a separate judge subprocess evaluating routing. The executor reads this file after task completion and uses it as the routing decision.

## Acceptance Criteria
- [ ] _use_judge(flow, node) helper resolves effective judge setting
- [ ] When judge disabled: routing instructions appended to task prompts
- [ ] When judge disabled: DECISION.json read from task_dir after completion
- [ ] When judge enabled: existing judge path used (no change)
- [ ] Node-level judge overrides flow-level

## Technical Design

### Files to Modify
- `src/flowstate/engine/executor.py` — add _use_judge(), branch in _handle_conditional/_handle_default_edge
- `src/flowstate/engine/context.py` — append routing instructions to prompts when judge disabled

### Key Implementation Details
- `_use_judge(flow, node)`: `return node.judge if node.judge is not None else flow.judge`
- When judge disabled, append to task prompt: available edges, instruction to write DECISION.json
- Read DECISION.json using existing `read_judge_decision()` from judge.py

## Testing Strategy
- tests/engine/test_executor.py — test self-report routing path
