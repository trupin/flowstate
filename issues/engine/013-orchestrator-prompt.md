# [ENGINE-013] Orchestrator Prompt Template

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-012
- Blocks: ENGINE-014

## Spec References
- specs.md Section 9 — "Claude Code Integration"
- specs.md Section 6 — "Execution Model"

## Summary
Design the orchestrator's system prompt that gives it: the flow graph (nodes, edges, conditions) serialized from the AST, its responsibilities (spawn subagents for tasks, evaluate transitions as judge), the file communication protocol (read INPUT.md, ensure SUMMARY.md, write DECISION.json), judge evaluation format, fork handling via parallel Agent tool calls, and subagent model configuration.

## Acceptance Criteria
- [ ] `build_orchestrator_system_prompt(flow, run_data_dir, cwd) -> str` function exists in context.py
- [ ] Prompt includes serialized flow graph (nodes with types, edges with conditions)
- [ ] Prompt describes the orchestrator's two responsibilities: task execution and judge evaluation
- [ ] Prompt specifies the file protocol (INPUT.md, SUMMARY.md, DECISION.json paths)
- [ ] Prompt includes judge decision format: `{ "decision", "reasoning", "confidence" }`
- [ ] Prompt instructs orchestrator to spawn subagents with `model: "opus"`
- [ ] Prompt instructs orchestrator to use parallel Agent tool for fork branches
- [ ] `serialize_flow_graph(flow) -> str` helper produces a readable text summary of the flow
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/context.py` — Add `build_orchestrator_system_prompt()`, `serialize_flow_graph()`
- `tests/engine/test_orchestrator_prompt.py` — Tests

### Key Implementation Details

#### Flow Graph Serialization

```python
def serialize_flow_graph(flow: Flow) -> str:
    """Serialize a Flow AST into a readable text representation for the orchestrator."""
    # Lists all nodes (with type and prompt summary) and all edges (with conditions)
```

#### Orchestrator System Prompt Template

The prompt should cover:
1. **Identity**: "You are a Flowstate orchestrator agent managing a flow run."
2. **Flow graph**: Full node/edge topology so orchestrator understands the workflow
3. **Task execution protocol**: Read INPUT.md, spawn subagent with Agent tool, ensure SUMMARY.md exists
4. **Judge evaluation protocol**: Read REQUEST.md, evaluate conditions, write DECISION.json
5. **Subagent configuration**: Use `model: "opus"` for subagents
6. **Fork handling**: Use parallel Agent tool calls for concurrent tasks
7. **File paths**: Run data directory, task directory pattern

### Edge Cases
- Flow with no conditional edges (orchestrator never acts as judge)
- Flow with only one node (trivial flow)
- Very long prompts in nodes — summarize/truncate for flow graph serialization
- Template variables in prompts — show expanded form

## Testing Strategy
1. **test_serialize_flow_graph_linear** — 3-node linear flow, verify all nodes and edges listed
2. **test_serialize_flow_graph_conditional** — Flow with conditions, verify conditions in output
3. **test_serialize_flow_graph_fork** — Fork-join flow, verify fork targets and join listed
4. **test_build_orchestrator_system_prompt** — Verify prompt contains all required sections
5. **test_build_orchestrator_system_prompt_includes_paths** — Verify run_data_dir and cwd in prompt
