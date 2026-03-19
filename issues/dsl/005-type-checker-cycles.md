# [DSL-005] Type Checker (cycle rules C1-C3)

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
- specs.md Section 4.3 — "Cycle Rules"
- specs.md Section 4.5 — "Validation Algorithm" (step 5)
- specs.md Section 2.8 — "Generation" (cycle re-entry context)

## Summary
Implement the cycle validation rules (C1-C3) in the type checker. Cycles occur when a conditional edge targets a node that has already been visited in the flow graph (a "back-edge" in DFS terminology). These rules ensure cycles are safe: they must not target nodes inside fork-join groups (which would create ambiguous join semantics), every cycle must pass through at least one conditional edge (preventing unconditional infinite loops), and flows with cycles must declare a budget (providing a time guard for potentially infinite execution).

## Acceptance Criteria
- [ ] `_check_cycles(flow)` in `type_checker.py` implements all 3 rules
- [ ] Cycle detection: correctly identifies back-edges in the flow graph using DFS
- [ ] C1: Cycle target nodes must be outside any fork-join group. Error if a cycle targets a node that is a fork target (inside a fork-join region).
- [ ] C2: Every cycle must pass through at least one conditional edge. Error if any cycle path consists entirely of unconditional edges.
- [ ] C3: Flows that contain any cycle must declare a `budget`. Error if `budget_seconds` is missing/zero and cycles exist. (Note: budget is required by the grammar, so this rule catches the case where budget might be set to zero or a future grammar change makes it optional.)
- [ ] One negative test per rule (3 tests minimum)
- [ ] Valid cycle flows (Appendix A.3, A.4, A.5) produce no C-rule errors
- [ ] Acyclic flows produce no C-rule errors

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/type_checker.py` — implement `_check_cycles(flow)` (replace the stub from DSL-003)
- `tests/dsl/test_type_checker.py` — add C1-C3 tests

### Key Implementation Details

**Cycle detection via DFS**:

Use iterative DFS with a recursion stack to find back-edges (edges that point to a node currently on the DFS stack). Each back-edge defines a cycle.

```python
def _find_cycles(flow: Flow) -> list[list[str]]:
    """Find all cycles in the flow graph.

    Returns a list of cycles, where each cycle is a list of node names
    forming the cycle path (from cycle target back to itself).
    """
    outgoing = _build_outgoing_adjacency(flow)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in flow.nodes}
    parent: dict[str, str | None] = {name: None for name in flow.nodes}
    cycles: list[list[str]] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        for neighbor in outgoing.get(node, []):
            if color[neighbor] == GRAY:
                # Back-edge found: reconstruct cycle
                cycle = _reconstruct_cycle(parent, node, neighbor)
                cycles.append(cycle)
            elif color[neighbor] == WHITE:
                parent[neighbor] = node
                dfs(neighbor)
        color[node] = BLACK

    # Start from entry node
    entry = _get_entry(flow)
    if entry:
        dfs(entry.name)

    return cycles
```

Note: Use iterative DFS instead of recursive to avoid stack overflow on large graphs:
```python
def _find_back_edges(flow: Flow) -> list[tuple[str, str]]:
    """Find all back-edges (source, target) that form cycles."""
    outgoing = _build_outgoing_adjacency(flow)

    back_edges: list[tuple[str, str]] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in flow.nodes}
    stack: list[tuple[str, bool]] = []  # (node, is_backtrack)

    entry = _get_entry(flow)
    if not entry:
        return []

    stack.append((entry.name, False))

    while stack:
        node, is_backtrack = stack.pop()
        if is_backtrack:
            color[node] = BLACK
            continue

        if color[node] == GRAY:
            continue

        color[node] = GRAY
        stack.append((node, True))  # Push backtrack marker

        for neighbor in outgoing.get(node, []):
            if color[neighbor] == GRAY:
                back_edges.append((node, neighbor))
            elif color[neighbor] == WHITE:
                stack.append((neighbor, False))

    return back_edges
```

**C1 — Cycle targets must be outside fork-join groups**:

First, identify all fork-join groups: for each fork edge, the "fork-join group" consists of the fork targets (the nodes between the fork and the join). A cycle back-edge targets a node -- if that target is one of the fork targets, it violates C1.

```python
# Build set of nodes inside fork-join groups
fork_group_members: set[str] = set()
for edge in flow.edges:
    if edge.edge_type == EdgeType.FORK and edge.fork_targets:
        fork_group_members.update(edge.fork_targets)

# Check each back-edge
for source, target in back_edges:
    if target in fork_group_members:
        errors.append(FlowTypeError(
            "C1",
            f"Cycle edge '{source}' -> '{target}' targets node '{target}' which is inside a fork-join group. "
            f"Cycle targets must be outside fork-join groups.",
            target
        ))
```

Per the spec's C1 explanation: "If nodes B and C are forked (A -> [B, C]) and joined ([B, C] -> D), no cycle edge may target B or C directly. The cycle must target A (the fork source) or any node before A."

**C2 — Every cycle must pass through at least one conditional edge**:

For each back-edge (forming a cycle), trace the cycle path and check that at least one edge along the path is conditional. The cycle path goes from the back-edge target forward through the graph back to the back-edge source, then the back-edge itself.

```python
def _cycle_has_conditional(flow: Flow, back_edge_source: str, back_edge_target: str) -> bool:
    """Check if the cycle formed by this back-edge passes through a conditional edge."""
    # The back-edge itself might be conditional
    for edge in flow.edges:
        if _edge_connects(edge, back_edge_source, back_edge_target):
            if edge.edge_type == EdgeType.CONDITIONAL:
                return True

    # Check edges along the path from target to source (the forward path)
    # BFS/DFS from back_edge_target to back_edge_source, checking if any
    # edge along a path to the source is conditional
    # ...
    return False
```

A simpler approach: for each back-edge, reconstruct the cycle by tracing the DFS tree from target to source. Then check all edges in the cycle. If none are conditional, report C2.

Alternatively, since finding exact cycle paths can be complex with multiple paths, use this conservative approach: for each back-edge, check whether the back-edge itself is conditional. If it is, the cycle is safe. If it isn't, trace one path from the back-edge target to the back-edge source and check for any conditional edge along that path. If no conditional edge is found on any path, report C2. In practice, most cycles in Flowstate have the conditional edge as the back-edge itself (e.g., `review -> implement when "needs more work"`).

**Practical simplification**: The most common pattern is `A -> B -> A when "condition"`. The back-edge `A when "condition" -> B` is itself conditional. Check the back-edge first; if it's conditional, skip further analysis. Only dig deeper for unconditional back-edges.

```python
for source, target in back_edges:
    # Find the actual edge object for this back-edge
    back_edge_obj = _find_edge(flow, source, target)
    if back_edge_obj and back_edge_obj.edge_type == EdgeType.CONDITIONAL:
        continue  # This cycle has a conditional edge

    # Unconditional back-edge: check if the forward path has a conditional
    if not _forward_path_has_conditional(flow, target, source):
        errors.append(FlowTypeError(
            "C2",
            f"Cycle from '{source}' back to '{target}' has no conditional edge. "
            f"Every cycle must pass through at least one conditional edge to prevent infinite loops.",
            source
        ))
```

**C3 — Flows with cycles must declare a budget**:

Since `budget` is a required flow attribute (the parser enforces this), C3 serves as a safeguard. If the budget is ever allowed to be optional, this check prevents unbounded execution. For now, check that `budget_seconds > 0`:

```python
if back_edges:  # Flow has cycles
    if flow.budget_seconds <= 0:
        errors.append(FlowTypeError(
            "C3",
            "Flows with cycles must declare a budget (budget must be > 0)",
            ""
        ))
```

If budget is always required by the parser, this rule may never trigger in practice, but implement it for completeness.

### Edge Cases
- Self-loop: `A -> A when "retry"` -- this is a valid cycle (A is its own target). C1 should not flag it unless A is a fork target. C2 requires the edge to be conditional (it is).
- Multiple cycles in the same flow: each must be checked independently
- Cycle that includes the entry node (violates S6 if entry has incoming -- S6 catches this)
- Cycle within a fork-join group targeting the fork source (valid: the source is outside the group)
- Nested cycles: `A -> B -> C -> A` and `B -> D -> B` (two separate cycles)
- Cycle that shares nodes with a fork-join group but targets a node outside the group (valid)
- Acyclic flow: `_find_back_edges` returns empty list, all C-rules trivially pass
- Diamond graph (A -> B, A -> C, B -> D, C -> D): not a cycle, should not trigger C-rules

## Testing Strategy

Add tests to `tests/dsl/test_type_checker.py`.

**Negative tests (one per rule)**:

1. **C1 — cycle into fork group**: Build a flow with:
   - entry -> A -> [B, C] (fork)
   - [B, C] -> D (join)
   - D -> B when "retry" (cycle targets B, which is inside the fork group)

   Assert `FlowTypeError` with `rule == "C1"`.

2. **C2 — unconditional cycle**: Build a flow with:
   - entry -> A -> B -> A (unconditional back-edge B -> A)
   - A -> exit when "done"

   The cycle A -> B -> A has no conditional edge. Assert `FlowTypeError` with `rule == "C2"`.

3. **C3 — cycle without budget**: Build a `Flow` object directly with `budget_seconds = 0` and a cycle. Assert `FlowTypeError` with `rule == "C3"`. (This requires constructing the Flow manually since the parser requires a budget.)

**Positive tests**:

4. **Valid cycle with conditional**: Build a flow with:
   - entry -> implement -> verify
   - verify -> exit when "done"
   - verify -> implement when "needs work"

   The cycle verify -> implement is conditional. Assert no C-rule errors.

5. **Valid self-loop**: Build a flow with:
   - entry -> check -> exit when "healthy"
   - check -> check when "not yet"

   Assert no C-rule errors.

6. **Acyclic flow**: Any linear or fork-join flow without cycles. Assert no C-rule errors.

7. **Appendix A.3 (iterative_refactor)**: Parse and check. Assert no C-rule errors.

8. **Appendix A.4 (feature_development)**: Has a cycle (review -> design). Assert no C-rule errors.

9. **Appendix A.5 (deploy_and_monitor)**: Has a self-loop cycle (check_health -> check_health). Assert no C-rule errors.
