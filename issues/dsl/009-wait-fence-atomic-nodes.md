# [DSL-009] Add wait, fence, and atomic node types + max_parallel flow attribute

## Domain
dsl

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: ENGINE-030, ENGINE-031, ENGINE-032

## Summary
Extend the DSL with three new node types (wait, fence, atomic) and a max_parallel flow attribute. Wait nodes pause the flow for a duration or until a cron time. Fence nodes synchronize parallel tasks at a barrier. Atomic nodes provide mutual exclusion across concurrent runs. max_parallel controls per-flow task queue concurrency.

## Acceptance Criteria
- [ ] Grammar accepts `wait NAME { delay = DURATION }` and `wait NAME { until = STRING }`
- [ ] Grammar accepts `fence NAME { }`
- [ ] Grammar accepts `atomic NAME { node_body }`
- [ ] Grammar accepts `max_parallel = NUMBER` as flow attribute
- [ ] AST: NodeType enum has WAIT, FENCE, ATOMIC
- [ ] AST: Flow has max_parallel: int = 1
- [ ] Parser builds correct AST for all new constructs
- [ ] Type checker validates wait nodes have delay or until (not both)

## Technical Design

### Files to Modify
- `src/flowstate/dsl/grammar.lark` — new node rules, flow attribute
- `src/flowstate/dsl/ast.py` — NodeType enum, Flow.max_parallel, Node wait fields
- `src/flowstate/dsl/parser.py` — transformers for new rules
- `src/flowstate/dsl/type_checker.py` — validation rules

## Testing Strategy
- `uv run pytest tests/dsl/ -v`
