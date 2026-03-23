# [DSL-010] Add files edge timing variants (after/at)

## Domain
dsl

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: DSL-009
- Blocks: ENGINE-033

## Summary
Extend `files` edges with timing variants: `after DURATION` (delay) and `at STRING` (cron schedule) for scheduling child tasks in the future.

## Acceptance Criteria
- [ ] Grammar accepts `NAME files NAME after DURATION`
- [ ] Grammar accepts `NAME files NAME at STRING`
- [ ] AST Edge stores delay_seconds or schedule for FILE edges
- [ ] Parser builds correct AST

## Technical Design
- `src/flowstate/dsl/grammar.lark` — edge_file_delayed, edge_file_scheduled rules
- `src/flowstate/dsl/ast.py` — Edge gains delay_seconds and schedule fields for FILE type
- `src/flowstate/dsl/parser.py` — transformers

## Testing Strategy
- `uv run pytest tests/dsl/ -v`
