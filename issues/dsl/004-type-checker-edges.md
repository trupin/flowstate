# [DSL-004] Type Checker (edge rules E1-E9)

## Domain
dsl

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: DSL-002
- Blocks: (none directly, but required for flow validation)

## Spec References
- specs.md Section 4.2 — "Edge Rules"
- specs.md Section 4.5 — "Validation Algorithm" (step 3)
- specs.md Section 3.5 — "Edge Declarations"
- specs.md Section 3.6 — "Context Modes"

## Summary
Implement the edge validation rules (E1-E9) in the type checker. These rules ensure edge semantics are consistent: nodes with a single outgoing edge must use unconditional edges, nodes with multiple outgoing edges must use either all-conditional or a single fork (never mixed), all edge references point to existing nodes, fork-join pairs match correctly, session mode is disallowed on fork/join edges, and delay/schedule are mutually exclusive with valid cron syntax.

## Acceptance Criteria
- [ ] `_check_edges(flow)` in `type_checker.py` implements all 9 rules
- [ ] E1: Node with exactly 1 outgoing edge must be unconditional. Error if it's conditional.
- [ ] E2: Node with 2+ outgoing edges must be all conditional OR a single fork. Error otherwise.
- [ ] E3: No mixing fork and conditional edges from the same node. Error if both types appear.
- [ ] E4: Every edge references existing nodes. Error for dangling references (source, target, fork_targets, join_sources).
- [ ] E5: Fork target set must match exactly one join with the same node set. Error if no matching join.
- [ ] E6: Join source set must match exactly one fork's target set. Error if no matching fork.
- [ ] E7: `context = session` not allowed on fork or join edges. Error if set.
- [ ] E8: `delay` and `schedule` are mutually exclusive on an edge. Error if both set.
- [ ] E9: `schedule` (cron) on an edge must be a valid cron expression. Error if invalid.
- [ ] One negative test per rule (9 tests minimum)
- [ ] Valid flows produce no E-rule errors

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/type_checker.py` — implement `_check_edges(flow)` (replace the stub from DSL-003)
- `tests/dsl/test_type_checker.py` — add E1-E9 tests

### Key Implementation Details

**E1 — Single outgoing edge must be unconditional**:

Group edges by source node. For each node with exactly 1 outgoing edge:
```python
# Build outgoing_edges: dict[str, list[Edge]] — maps source node name to its outgoing edges
for node_name, edges in outgoing_edges.items():
    if len(edges) == 1:
        edge = edges[0]
        if edge.edge_type == EdgeType.CONDITIONAL:
            errors.append(FlowTypeError(
                "E1",
                f"Node '{node_name}' has exactly 1 outgoing edge, which must be unconditional (not conditional)",
                node_name
            ))
```

Note: A single fork edge from a node is valid (it targets multiple nodes). E1 only flags conditional edges.

**E2 — Multiple outgoing edges: all conditional OR single fork**:

For each node with 2+ outgoing edges:
```python
if len(edges) >= 2:
    types = {e.edge_type for e in edges}
    all_conditional = all(e.edge_type == EdgeType.CONDITIONAL for e in edges)
    if not all_conditional:
        # The only other valid case is already handled by fork having a single edge
        # with multiple targets. If we see 2+ edges and they're not all conditional,
        # it's an error.
        errors.append(FlowTypeError(
            "E2",
            f"Node '{node_name}' has {len(edges)} outgoing edges: all must be conditional (when), or use a single fork",
            node_name
        ))
```

Important subtlety: A fork edge (`A -> [B, C]`) is a single `Edge` object with `edge_type=FORK`. It does NOT produce 2 separate edges from A. So if a node has 2+ entries in `outgoing_edges`, they should all be conditional. A fork node would have exactly 1 entry (the fork edge itself). This simplifies E2: 2+ outgoing edges => all must be conditional.

**E3 — No mixing fork and conditional from same node**:

This is a safety check beyond E2. A node should not have both a fork edge and conditional edges:
```python
has_fork = any(e.edge_type == EdgeType.FORK for e in edges)
has_conditional = any(e.edge_type == EdgeType.CONDITIONAL for e in edges)
if has_fork and has_conditional:
    errors.append(FlowTypeError(
        "E3",
        f"Node '{node_name}' mixes fork and conditional edges, which is not allowed",
        node_name
    ))
```

Also check for mixing unconditional with conditional or fork with unconditional from the same node with 2+ edges.

**E4 — All edge references point to existing nodes**:

For every edge, check that source, target, fork_targets, and join_sources all exist in `flow.nodes`:
```python
def _check_node_ref(name: str, edge: Edge, role: str) -> FlowTypeError | None:
    if name not in flow.nodes:
        return FlowTypeError(
            "E4",
            f"Edge references non-existent node '{name}' as {role}",
            name
        )
    return None

for edge in flow.edges:
    if edge.source and edge.source not in flow.nodes:
        errors.append(...)
    if edge.target and edge.target not in flow.nodes:
        errors.append(...)
    if edge.fork_targets:
        for t in edge.fork_targets:
            if t not in flow.nodes:
                errors.append(...)
    if edge.join_sources:
        for s in edge.join_sources:
            if s not in flow.nodes:
                errors.append(...)
```

Run E4 early -- if references are dangling, E5/E6 will produce confusing secondary errors.

**E5 — Fork target set must match exactly one join**:

Collect all fork edges and join edges. For each fork edge, find a join edge whose `join_sources` set equals the fork's `fork_targets` set:
```python
forks = [e for e in flow.edges if e.edge_type == EdgeType.FORK]
joins = [e for e in flow.edges if e.edge_type == EdgeType.JOIN]

for fork in forks:
    fork_set = set(fork.fork_targets)
    matching_joins = [j for j in joins if set(j.join_sources) == fork_set]
    if len(matching_joins) == 0:
        targets = ", ".join(fork.fork_targets)
        errors.append(FlowTypeError(
            "E5",
            f"Fork from '{fork.source}' to [{targets}] has no matching join edge",
            fork.source
        ))
    elif len(matching_joins) > 1:
        errors.append(FlowTypeError(
            "E5",
            f"Fork from '{fork.source}' has {len(matching_joins)} matching joins (expected exactly 1)",
            fork.source
        ))
```

**E6 — Join source set must match exactly one fork**:

Mirror of E5:
```python
for join in joins:
    join_set = set(join.join_sources)
    matching_forks = [f for f in forks if set(f.fork_targets) == join_set]
    if len(matching_forks) == 0:
        sources = ", ".join(join.join_sources)
        errors.append(FlowTypeError(
            "E6",
            f"Join to '{join.target}' from [{sources}] has no matching fork edge",
            join.target
        ))
    elif len(matching_forks) > 1:
        errors.append(FlowTypeError(
            "E6",
            f"Join to '{join.target}' has {len(matching_forks)} matching forks (expected exactly 1)",
            join.target
        ))
```

**E7 — Session not allowed on fork or join edges**:

```python
for edge in flow.edges:
    if edge.config.context == ContextMode.SESSION:
        if edge.edge_type in (EdgeType.FORK, EdgeType.JOIN):
            loc = f"fork from '{edge.source}'" if edge.edge_type == EdgeType.FORK else f"join to '{edge.target}'"
            errors.append(FlowTypeError(
                "E7",
                f"context = session is not allowed on {edge.edge_type.value} edges ({loc})",
                loc
            ))
```

Also check the flow-level default: if `flow.context == ContextMode.SESSION`, fork and join edges that don't override it would inherit session mode. The type checker should flag this. Either: (a) require fork/join edges to explicitly set `context = handoff` or `context = none` when the flow default is session, or (b) treat E7 as only applying to explicitly set edge-level context. The spec says "context = session is not allowed on fork or join edges" -- this means the effective context (including inherited) must not be session. Check both explicit and inherited:
```python
def _effective_context(edge: Edge, flow: Flow) -> ContextMode:
    return edge.config.context if edge.config.context is not None else flow.context

for edge in flow.edges:
    if edge.edge_type in (EdgeType.FORK, EdgeType.JOIN):
        effective = _effective_context(edge, flow)
        if effective == ContextMode.SESSION:
            errors.append(...)
```

**E8 — delay and schedule mutually exclusive**:
```python
for edge in flow.edges:
    if edge.config.delay_seconds is not None and edge.config.schedule is not None:
        source = edge.source or (", ".join(edge.join_sources or []))
        errors.append(FlowTypeError(
            "E8",
            f"Edge from '{source}' has both delay and schedule, which are mutually exclusive",
            source
        ))
```

**E9 — schedule must be valid cron**:

Use the `croniter` library (listed as a project dependency) to validate cron expressions:
```python
from croniter import croniter

for edge in flow.edges:
    if edge.config.schedule is not None:
        if not croniter.is_valid(edge.config.schedule):
            errors.append(FlowTypeError(
                "E9",
                f"Edge has invalid cron expression: '{edge.config.schedule}'",
                edge.source or ""
            ))
```

If `croniter` is not available, use a regex-based validator or catch exceptions from `croniter()` construction. The spec mentions `croniter` in Section 5.6.1.

### Edge Cases
- Node with 0 outgoing edges that isn't an exit (should be caught by S4, not E-rules)
- Fork edge with only 1 target (unusual but grammatically possible -- semantically it's just a normal edge)
- Join edge with only 1 source (unusual)
- Edge where source == target (self-loop): valid for conditional cycles (e.g., `check_health -> check_health when "still starting"`)
- Multiple conditional edges from same node to same target with different conditions (valid)
- Fork edge to a node that appears in another fork group (caught by F1, not E-rules)
- Cron expression with 6 fields (some cron implementations support seconds) -- follow `croniter` behavior
- Empty fork targets or join sources (grammar should prevent, but defend)

## Testing Strategy

Add tests to `tests/dsl/test_type_checker.py`.

**Negative tests (one per rule)**:

1. **E1 — single conditional**: Flow with node A having exactly 1 outgoing conditional edge `A -> B when "condition"`. Assert `FlowTypeError` with `rule == "E1"`.

2. **E2 — mixed unconditional + conditional**: Flow where node A has 1 unconditional edge `A -> B` and 1 conditional edge `A -> C when "condition"`. Assert `FlowTypeError` with `rule == "E2"`.

3. **E3 — mixed fork + conditional**: Flow where node A has a fork edge `A -> [B, C]` and a conditional edge `A -> D when "condition"`. Assert `FlowTypeError` with `rule == "E3"`.

4. **E4 — dangling reference**: Flow with edge `analyze -> nonexistent`. Assert `FlowTypeError` with `rule == "E4"`.

5. **E5 — unmatched fork**: Flow with fork `A -> [B, C]` but no matching join `[B, C] -> D`. Assert `FlowTypeError` with `rule == "E5"`.

6. **E6 — unmatched join**: Flow with join `[B, C] -> D` but no matching fork. Assert `FlowTypeError` with `rule == "E6"`.

7. **E7 — session on fork**: Flow with fork edge that has `context = session` in its config, OR flow with `context = session` default and a fork edge with no override. Assert `FlowTypeError` with `rule == "E7"`.

8. **E8 — delay + schedule**: Edge with both `delay = 5m` and `schedule = "0 * * * *"`. Assert `FlowTypeError` with `rule == "E8"`.

9. **E9 — invalid cron**: Edge with `schedule = "not a cron"`. Assert `FlowTypeError` with `rule == "E9"`.

**Positive tests**:

10. **Valid conditional branching**: Flow with 2 conditional edges from same node. Assert no E-rule errors.
11. **Valid fork-join**: Flow with matching fork and join. Assert no E-rule errors.
12. **Valid edge config**: Edge with `context = handoff` and `delay = 5m`. Assert no E-rule errors.
13. **Valid cron**: Edge with `schedule = "0 2 * * *"`. Assert no E-rule errors.

Construct test flows using direct `Flow`/`Node`/`Edge` construction for precise control, or parse `.flow` fixtures for integration-level tests.
