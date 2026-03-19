# [DSL-006] Type Checker (fork-join rules F1-F3)

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
- specs.md Section 4.4 — "Fork-Join Rules"
- specs.md Section 4.5 — "Validation Algorithm" (step 4)
- specs.md Section 2.3 — "Edge" (fork/join semantics)

## Summary
Implement the fork-join validation rules (F1-F3) in the type checker. Fork-join groups define parallel execution regions in the flow graph. These rules ensure that parallel regions have clean scoping: groups may nest but must not partially overlap (e.g., a node cannot belong to two independent fork groups), a join node cannot simultaneously be a fork source in the same declaration, and fork targets must converge to a single join (no "fire and forget" parallel paths).

## Acceptance Criteria
- [ ] `_check_fork_join(flow)` in `type_checker.py` implements all 3 rules
- [ ] F1: Fork groups may nest but must not partially overlap. Error if any node appears in two fork groups that are not in a proper nesting relationship.
- [ ] F2: A join node cannot also be a fork source in the same edge declaration. Error if the same node appears as both a join target and a fork source. (Separate edge declarations are fine: `[B, C] -> D` then `D -> [E, F]`.)
- [ ] F3: Fork targets must converge to a single join. Error if no matching join exists for a fork. (This overlaps with E5, but F3 emphasizes the convergence requirement.)
- [ ] One negative test per rule (3 tests minimum)
- [ ] Valid nested fork-join passes (e.g., the nested example from spec Section 4.4)
- [ ] Valid flows with fork-join (Appendix A.2, A.4) produce no F-rule errors
- [ ] Flows without any fork-join produce no F-rule errors

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/type_checker.py` — implement `_check_fork_join(flow)` (replace the stub from DSL-003)
- `tests/dsl/test_type_checker.py` — add F1-F3 tests

### Key Implementation Details

**Identifying fork-join groups**:

A fork-join group is defined by a fork edge and its matching join edge. The group members are the fork targets (the nodes that execute in parallel between the fork and the join).

```python
@dataclass
class ForkJoinGroup:
    fork_source: str          # Node that forks
    members: set[str]         # Fork target nodes (parallel execution)
    join_target: str          # Node that joins
    fork_edge: Edge
    join_edge: Edge
```

Build the list of groups:
```python
def _identify_fork_join_groups(flow: Flow) -> list[ForkJoinGroup]:
    forks = [e for e in flow.edges if e.edge_type == EdgeType.FORK]
    joins = [e for e in flow.edges if e.edge_type == EdgeType.JOIN]

    groups = []
    for fork in forks:
        fork_set = set(fork.fork_targets)
        for join in joins:
            if set(join.join_sources) == fork_set:
                groups.append(ForkJoinGroup(
                    fork_source=fork.source,
                    members=fork_set,
                    join_target=join.target,
                    fork_edge=fork,
                    join_edge=join,
                ))
                break
    return groups
```

**F1 — No partial overlap between fork groups**:

Two fork groups partially overlap if they share some but not all members, AND neither group's members are a proper subset of the other (which would indicate nesting).

Valid nesting example (from spec):
```
A -> [B, C]         # group 1 members: {B, C}
B -> [D, E]         # group 2 members: {D, E}
[D, E] -> B_done
[B_done, C] -> F
```
Here group 2 ({D, E}) does not share members with group 1 ({B, C}). This is nesting (group 2 is inside B's execution) but the member sets are disjoint, so there's no overlap.

Invalid partial overlap:
```
A -> [B, C]         # group 1 members: {B, C}
B -> [C, D]         # group 2 members: {C, D} — C is in BOTH groups
```
Here C appears in both groups. Group 1 has {B, C}, group 2 has {C, D}. Neither is a subset of the other. This is a partial overlap.

```python
def _check_f1(groups: list[ForkJoinGroup]) -> list[FlowTypeError]:
    errors = []
    for i, g1 in enumerate(groups):
        for g2 in groups[i + 1:]:
            intersection = g1.members & g2.members
            if intersection:
                # Groups share members. Check if one is a subset of the other (nesting).
                if not (g1.members <= g2.members or g2.members <= g1.members):
                    overlap = ", ".join(sorted(intersection))
                    errors.append(FlowTypeError(
                        "F1",
                        f"Fork groups partially overlap: fork from '{g1.fork_source}' "
                        f"and fork from '{g2.fork_source}' share nodes [{overlap}] "
                        f"but neither group is fully nested inside the other",
                        overlap
                    ))
    return errors
```

Note on nesting: True nesting means group 2's members are entirely contained within one member's "execution" of group 1. In the spec example, D and E are "inside" B's execution. But the member sets {B, C} and {D, E} are disjoint. So F1's overlap check on member sets works correctly for the spec's nesting pattern.

However, consider a case where nesting DOES cause subset membership:
```
A -> [B, C, D]      # group 1 members: {B, C, D}
B -> [C, D]          # group 2 members: {C, D}
```
Here {C, D} is a subset of {B, C, D}. But this is actually invalid because C and D appear in both groups -- they'd be forked twice. The subset check would allow this, but it shouldn't. Revisit: the spec says "Fork groups may be nested but must not partially overlap." A subset IS nesting (group 2 nested inside group 1). But the above example is problematic because C and D can't be in two active fork groups simultaneously.

The key insight: in valid nesting (spec example), the inner fork's targets are DIFFERENT nodes than the outer fork's targets. The inner fork happens inside one of the outer fork's members. So inner members {D, E} are disjoint from outer members {B, C}. Partial overlap means shared members, and ANY shared members between fork groups is invalid (not just partial -- even subset).

Correction: if g1.members and g2.members share any node, it's invalid. Nesting in the spec never produces shared members.

```python
def _check_f1(groups: list[ForkJoinGroup]) -> list[FlowTypeError]:
    errors = []
    for i, g1 in enumerate(groups):
        for g2 in groups[i + 1:]:
            intersection = g1.members & g2.members
            if intersection:
                overlap = ", ".join(sorted(intersection))
                errors.append(FlowTypeError(
                    "F1",
                    f"Fork groups overlap: fork from '{g1.fork_source}' and fork from "
                    f"'{g2.fork_source}' share member nodes [{overlap}]. "
                    f"Fork groups may nest but must not share member nodes.",
                    overlap
                ))
    return errors
```

**F2 — Join node cannot also be fork source in same declaration**:

This prevents a single edge declaration like `[B, C] -> D -> [E, F]`. The grammar doesn't support this syntax directly (join_edge is `"[" name_list "]" "->" NAME`), so this would manifest as two edges where the join target is the same node as a fork source. However, the spec says "in the same declaration" -- meaning a single edge statement can't combine join and fork. Since the grammar forces separate statements, F2 would only be violated if someone manually constructs a flow where a join_edge and fork_edge share the same intermediate node AND they're intended to be a single atomic operation.

In practice, this rule prevents the pattern where the join edge's target is the same as the fork edge's source when they reference the same fork-join "layer". The check:

```python
def _check_f2(flow: Flow) -> list[FlowTypeError]:
    errors = []
    join_targets = {e.target for e in flow.edges if e.edge_type == EdgeType.JOIN}
    fork_sources = {e.source for e in flow.edges if e.edge_type == EdgeType.FORK}

    # A node that is both a join target and fork source is fine as separate
    # declarations. F2 is about the same "declaration" -- since the grammar
    # forces separate edge statements, check if any join edge's target
    # immediately appears as a fork source (which IS valid per the spec:
    # "Separate the join and the next fork into distinct edge declarations").
    #
    # F2 as stated: "Join node cannot also be fork source in same declaration."
    # Since the grammar prevents this syntactically, this is mainly a safety
    # check for programmatically-constructed flows.

    # For grammar-parsed flows, this cannot happen. But for safety:
    for edge in flow.edges:
        if edge.edge_type == EdgeType.JOIN and edge.target:
            # Check if the same edge somehow also has fork semantics
            # (this shouldn't happen with the grammar, but guard against it)
            pass

    return errors
```

Since the grammar makes F2 violations impossible syntactically, this rule serves as a guard for programmatic AST construction. Implement the check but it will rarely fire in practice.

A more useful interpretation: verify that if a node N is both a join target AND a fork source, they are separate edge declarations (which is always true with the grammar). Log this as passing. The test can construct a Flow manually to trigger it.

**F3 — Fork targets must converge to a single join**:

This overlaps with E5 but emphasizes convergence. Every fork edge must have a corresponding join edge where ALL the fork targets converge. This is already checked by E5 (fork target set must match exactly one join's source set). F3 adds the semantic requirement that the convergence actually happens in the graph -- all fork targets must have paths that lead to the join target.

For a robust F3 check: verify that all fork target nodes can reach the join target node via forward edges:
```python
def _check_f3(flow: Flow, groups: list[ForkJoinGroup]) -> list[FlowTypeError]:
    errors = []
    outgoing = _build_outgoing_adjacency(flow)

    for group in groups:
        for member in group.members:
            # Check that this member can reach the join target
            reachable = _reachable_from(member, outgoing)
            if group.join_target not in reachable:
                errors.append(FlowTypeError(
                    "F3",
                    f"Fork target '{member}' (forked from '{group.fork_source}') "
                    f"cannot reach join node '{group.join_target}'. "
                    f"All fork targets must converge to a single join.",
                    member
                ))
    return errors
```

Note: The `_reachable_from` utility was created in DSL-003. Reuse it.

If no matching join exists at all, E5 catches it. F3 catches the case where a join is declared but the graph topology doesn't actually allow convergence (e.g., a fork member has an exit path that bypasses the join).

### Edge Cases
- Flow with no fork-join edges: all F-rules trivially pass
- Single fork-join group: straightforward check
- Multiple independent fork-join groups (not nested): members should be disjoint
- Nested fork-join (spec example: A -> [B, C], B -> [D, E], [D, E] -> B_done, [B_done, C] -> F): valid nesting, no shared members
- Fork with 2 targets: minimal case
- Fork with many targets (5+): should work the same
- Fork member that is also an exit node: violates S7 (exit has outgoing -- the join requires it to "transition") or F3 (can't converge). S7 and the edge rules should catch this.
- Join target that is also the fork source (cycle through fork): valid if the cycle targets the fork source (outside the group)

## Testing Strategy

Add tests to `tests/dsl/test_type_checker.py`.

**Negative tests (one per rule)**:

1. **F1 — partial overlap**: Build a flow with:
   ```
   entry -> A (unconditional)
   A -> [B, C] (fork)
   B -> [C, D] (fork) -- C appears in both groups
   [C, D] -> E (join)
   [B, E] -> exit -- this join doesn't match the first fork
   ```
   This has two fork groups: {B, C} and {C, D}. C appears in both. Assert `FlowTypeError` with `rule == "F1"`.

   Simpler construction: build the `Flow` directly with two fork edges whose target sets share a member node.

2. **F2 — join target is fork source in same construct**: Build a `Flow` object directly where an `Edge` with `edge_type=JOIN` has `target="D"` and another `Edge` with `edge_type=FORK` has `source="D"` and `fork_targets=["E", "F"]`. While this is valid as separate declarations, create a test to verify F2 is checked. Since the grammar prevents the single-declaration violation, construct the scenario programmatically: create a flow and mark it as violating F2 by some mechanism (e.g., a flag, or combine the join_sources and fork_targets on a single Edge object). Alternatively, accept that F2 is a grammar-level guarantee and test that the check function exists and correctly passes for the valid-but-looks-similar case: `[B, C] -> D` followed by `D -> [E, F]` should NOT trigger F2.

   Practical approach: Construct a single `Edge` that has both `join_sources` AND `fork_targets` set (which represents the prohibited combined declaration). The type checker should detect this malformed edge and report F2.

3. **F3 — fork targets don't converge**: Build a flow with:
   ```
   entry -> A (unconditional)
   A -> [B, C] (fork)
   [B, C] -> D (join)
   B -> exit (unconditional) -- B exits instead of reaching D
   ```
   Wait, B has two outgoing edges (one to the join, implicitly, and one to exit). That's an E2 issue. Better:

   Build a flow where a fork is declared but one fork target has no path to the join target. The simplest case: construct directly with a fork `A -> [B, C]`, a join `[B, C] -> D`, but node C has no outgoing edges and is not the join target. Actually with the join declared, C IS a join source. The graph edges from C would need to reach D (the join target). If C has an outgoing edge to `exit` instead of eventually reaching D, F3 should flag it.

   Simpler approach: construct the Flow directly with a fork edge and join edge, but don't add any edges FROM one of the fork targets that would lead to the join target. The fork target becomes a dead end. Assert `FlowTypeError` with `rule == "F3"`.

**Positive tests**:

4. **Valid fork-join**: Simple `A -> [B, C]`, `[B, C] -> D` with B and C having paths to D. Assert no F-rule errors.

5. **Valid nested fork-join**: Reproduce the spec example:
   ```
   A -> [B, C]
   B -> [D, E]
   [D, E] -> B_done
   [B_done, C] -> F
   ```
   Assert no F-rule errors. Member sets {B, C} and {D, E} are disjoint.

6. **Valid Appendix A.2 (fork-join flow)**: Parse and check. Assert no F-rule errors.

7. **Valid Appendix A.4 (fork-join with cycle)**: Parse and check. Assert no F-rule errors.

8. **No fork-join**: Linear flow (Appendix A.1). Assert no F-rule errors.

9. **F2 valid separation**: Flow with `[B, C] -> D` and `D -> [E, F]` as separate edges. Assert no F2 error -- the join and fork are separate declarations.
