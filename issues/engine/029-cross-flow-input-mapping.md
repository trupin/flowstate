# [ENGINE-029] Cross-flow task filing must map to target flow's declared input fields

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SHARED-004
- Blocks: —

## Summary
When a node files a task to another flow via a `files` or `awaits` edge, it currently inherits the parent task's params blindly. Instead, it should look up the target flow's declared `input { ... }` fields and construct the child task's params to match. The filing node's SUMMARY.md or output should be mapped to the target flow's expected input fields.

For example, if `review files bugfix` and `bugfix` declares `input { bug_report: string, severity: string }`, the executor should prompt the agent to produce output that matches those fields, or map from the source node's output to the target's input.

## Acceptance Criteria
- [ ] `files`/`awaits` edges look up the target flow's input field declarations
- [ ] Child task's `params_json` contains values for the target flow's declared input fields
- [ ] If the source node's output doesn't provide all required input fields, the task is created with available data and missing fields noted
- [ ] The filing prompt instructs the agent to produce output matching the target flow's input schema
- [ ] Type checker can optionally validate that `files`/`awaits` edges reference flows with compatible I/O (deferred to runtime initially)

## Technical Design

### Files to Modify
- `src/flowstate/engine/executor.py` — `_handle_file_edge()` and `_handle_await_edges()`
- `src/flowstate/engine/context.py` — prompt instructions for cross-flow filing
- `src/flowstate/server/flow_registry.py` — expose target flow's input fields for lookup

### Key Implementation Details

**Current behavior** (`_handle_file_edge`):
```python
# Blindly copies parent params
child_params = json.loads(parent_task.params_json) if parent_task.params_json else {}
```

**Desired behavior:**
1. Look up the target flow in the registry: `registry.get_flow_by_name(edge.target)`
2. Parse its DSL to get `flow_ast.input_fields`
3. Map the source node's output (SUMMARY.md, OUTPUT.json) to the target's input fields
4. If OUTPUT.json has fields matching the target's input names, use them
5. Otherwise, pass SUMMARY.md content as the first string input field

**Prompt enhancement** — when a node has outgoing `files`/`awaits` edges, the prompt should include:
```
## Cross-flow output
This task will file a task to flow "{target_flow}".
That flow expects these inputs:
  - bug_report: string (required)
  - severity: string (default: "medium")
Include these in your OUTPUT.json so they can be passed to the target flow.
```

### Edge Cases
- Target flow not found in registry → log warning, create task with empty params
- Target flow has no input fields → create task with no params
- Source output fields don't match target input names → pass what's available

## Testing Strategy
- Unit test: file edge maps source OUTPUT.json to target input fields
- Unit test: missing target flow → warning, task still created
- E2E: review flow files task to bugfix flow → bugfix gets correct input params
