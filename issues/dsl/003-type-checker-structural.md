# [DSL-003] Type Checker (structural rules S1-S8)

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
- specs.md Section 4.1 — "Structural Rules"
- specs.md Section 4.5 — "Validation Algorithm" (steps 1-2, 6)
- specs.md Section 12.2 — "Type Check Errors"

## Summary
Implement the structural validation rules (S1-S8) in the type checker. These rules verify the fundamental graph topology of a flow: exactly one entry node, at least one exit node, all nodes reachable, all nodes can reach an exit, no duplicate names, entry has no incoming edges, exit has no outgoing edges, and every node has a resolvable working directory. These are the foundational checks that must pass before edge, cycle, or fork-join rules can be meaningfully evaluated.

## Acceptance Criteria
- [ ] `src/flowstate/dsl/type_checker.py` exists with a `check_flow(flow: Flow) -> list[FlowTypeError]` function
- [ ] `FlowTypeError` is defined (in `exceptions.py` or `type_checker.py`) with fields: `rule` (str, e.g., "S1"), `message` (str), `location` (str, node/edge name)
- [ ] S1: Exactly one `entry` node. Error if zero or more than one.
- [ ] S2: At least one `exit` node. Error if zero.
- [ ] S3: All nodes reachable from entry via BFS/DFS. Error lists unreachable nodes.
- [ ] S4: At least one exit reachable from every node. Error lists nodes that cannot reach any exit.
- [ ] S5: No duplicate node names. Error lists the duplicate name.
- [ ] S6: Entry node has no incoming edges. Error if any edge targets the entry.
- [ ] S7: Exit nodes have no outgoing edges. Error if any edge has an exit as source.
- [ ] S8: Every node has a resolvable cwd — either its own `cwd` attribute or the flow-level `workspace`. Error lists nodes with no resolvable cwd.
- [ ] `check_flow` returns an empty list for all valid Appendix A flows (A.1-A.6)
- [ ] One negative test per rule (8 tests minimum)
- [ ] Type errors include the rule ID (e.g., "S1") in the `rule` field for programmatic matching

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/type_checker.py` — `check_flow()` function and rule implementations
- `src/flowstate/dsl/exceptions.py` — add `FlowTypeError` dataclass
- `tests/dsl/test_type_checker.py` — tests for S1-S8
- `tests/dsl/fixtures/` — additional invalid fixture flows as needed

### Key Implementation Details

**`FlowTypeError` dataclass**:
```python
@dataclass
class FlowTypeError:
    rule: str       # e.g., "S1", "E3", "C2"
    message: str    # human-readable description
    location: str   # node name, edge description, or empty
```

**`check_flow` function structure**:
```python
def check_flow(flow: Flow) -> list[FlowTypeError]:
    errors: list[FlowTypeError] = []
    errors.extend(_check_structural(flow))
    errors.extend(_check_edges(flow))        # DSL-004
    errors.extend(_check_cycles(flow))       # DSL-005
    errors.extend(_check_fork_join(flow))    # DSL-006
    return errors
```

For this issue, only implement `_check_structural(flow)`. The other functions should exist as stubs returning empty lists so that `check_flow` is callable end-to-end. DSL-004, DSL-005, and DSL-006 will fill them in.

**Building the adjacency list** (shared utility for all rules):
```python
def _build_adjacency(flow: Flow) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Returns (outgoing, incoming) adjacency dicts.

    outgoing[node_name] = list of successor node names
    incoming[node_name] = list of predecessor node names
    """
```

For each edge:
- `UNCONDITIONAL` / `CONDITIONAL`: add `source -> target` to outgoing, `target -> source` to incoming
- `FORK`: add `source -> each target` to outgoing, `each target -> source` to incoming
- `JOIN`: add `each source -> target` to outgoing, `target -> each source` to incoming

**Rule implementations**:

**S1 — Exactly one entry node**:
```python
entries = [n for n in flow.nodes.values() if n.node_type == NodeType.ENTRY]
if len(entries) == 0:
    errors.append(FlowTypeError("S1", "Flow must have exactly one entry node, found none", ""))
elif len(entries) > 1:
    names = ", ".join(n.name for n in entries)
    errors.append(FlowTypeError("S1", f"Flow must have exactly one entry node, found {len(entries)}: {names}", entries[1].name))
```

**S2 — At least one exit node**:
```python
exits = [n for n in flow.nodes.values() if n.node_type == NodeType.EXIT]
if len(exits) == 0:
    errors.append(FlowTypeError("S2", "Flow must have at least one exit node", ""))
```

**S3 — All nodes reachable from entry** (BFS):
```python
from collections import deque

def _reachable_from(start: str, outgoing: dict[str, list[str]]) -> set[str]:
    visited = set()
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for neighbor in outgoing.get(node, []):
            queue.append(neighbor)
    return visited

reachable = _reachable_from(entry_name, outgoing)
unreachable = set(flow.nodes.keys()) - reachable
for name in sorted(unreachable):
    errors.append(FlowTypeError("S3", f"Node '{name}' is not reachable from entry", name))
```

**S4 — At least one exit reachable from every node**:
Do a reverse BFS from all exit nodes using the `incoming` adjacency. Any node not in the reverse-reachable set cannot reach any exit.
```python
exit_names = {n.name for n in flow.nodes.values() if n.node_type == NodeType.EXIT}
# BFS backward from all exits
reverse_reachable = set()
queue = deque(exit_names)
while queue:
    node = queue.popleft()
    if node in reverse_reachable:
        continue
    reverse_reachable.add(node)
    for pred in incoming.get(node, []):
        queue.append(pred)

no_exit_path = set(flow.nodes.keys()) - reverse_reachable
for name in sorted(no_exit_path):
    errors.append(FlowTypeError("S4", f"Node '{name}' cannot reach any exit node", name))
```

**S5 — No duplicate node names**:
Since `flow.nodes` is a `dict[str, Node]`, duplicates would have been collapsed by the parser. The parser should detect duplicates during AST construction (in `flow_decl` when adding to the dict) and either raise `FlowParseError` or the type checker can track this. If the parser already prevents duplicates by overwriting, add a check in the transformer to detect it. Alternatively, the type checker can be a safety net: verify that the count of node declarations in edges matches the nodes dict. For robustness, implement this in the type checker by having the parser pass through a list and checking here.

**Implementation note**: The simplest approach is to have the parser build a list of nodes, then the type checker checks for duplicates before converting to a dict. Or, have the parser detect duplicates and raise. Either way, ensure S5 is testable. If the parser already prevents duplicates, create the test by constructing a `Flow` object directly with a duplicate node.

**S6 — Entry has no incoming edges**:
```python
entry_name = entries[0].name  # from S1 check
if incoming.get(entry_name):
    sources = ", ".join(incoming[entry_name])
    errors.append(FlowTypeError("S6", f"Entry node '{entry_name}' has incoming edges from: {sources}", entry_name))
```

**S7 — Exit nodes have no outgoing edges**:
```python
for exit_node in exits:
    if outgoing.get(exit_node.name):
        targets = ", ".join(outgoing[exit_node.name])
        errors.append(FlowTypeError("S7", f"Exit node '{exit_node.name}' has outgoing edges to: {targets}", exit_node.name))
```

**S8 — Every node has a resolvable cwd**:
```python
for node in flow.nodes.values():
    if node.cwd is None and flow.workspace is None:
        errors.append(FlowTypeError("S8", f"Node '{node.name}' has no cwd and flow has no workspace", node.name))
```

**Early termination**: If S1 fails (no entry node), S3, S4, and S6 cannot run meaningfully. Guard those checks:
```python
if len(entries) == 1:
    # run S3, S4, S6
```

### Edge Cases
- Flow with zero nodes (grammar might not allow, but defend against it)
- Flow with only entry and exit (no task nodes) -- valid
- Entry node that is also the only exit (unusual but the spec doesn't explicitly prohibit a single node being both entry and exit -- however the grammar uses separate `entry`/`exit` keywords so this is impossible)
- Disconnected subgraph: nodes with edges between them but not connected to entry
- Exit node with a self-loop edge (violates S7)
- Entry node targeted by a cycle edge (violates S6)
- Node with cwd set but flow also has workspace (valid: node cwd takes priority at runtime)
- Flow with workspace but some nodes also have cwd (valid)

## Testing Strategy

Create tests in `tests/dsl/test_type_checker.py`.

**Setup**: For each test, either parse a `.flow` fixture or construct a `Flow` object directly. Direct construction is simpler for targeted rule testing since you can create minimal invalid ASTs without writing full DSL source.

**Helper**: Create a helper to make minimal valid flows, then modify one aspect to trigger a specific rule:
```python
def make_flow(**overrides) -> Flow:
    """Create a minimal valid flow, then apply overrides."""
```

**Negative tests (one per rule)**:

1. **S1 — no entry**: Flow with only task and exit nodes. Assert error with `rule == "S1"`.
2. **S1 — multiple entries**: Flow with two entry nodes. Assert error with `rule == "S1"`.
3. **S2 — no exit**: Flow with entry and task nodes but no exit. Assert error with `rule == "S2"`.
4. **S3 — unreachable node**: Flow with entry -> exit, plus a disconnected task node. Assert error with `rule == "S3"` mentioning the disconnected node.
5. **S4 — node can't reach exit**: Flow where entry -> task_a -> task_b (dead end, no edge to exit), plus entry -> exit. task_b can't reach exit. Assert error with `rule == "S4"`.
6. **S5 — duplicate names**: Flow with two nodes named the same. Assert error with `rule == "S5"`. (May need to construct the Flow object directly if the parser prevents duplicates.)
7. **S6 — entry has incoming**: Flow where a task node has an edge back to the entry node. Assert error with `rule == "S6"`.
8. **S7 — exit has outgoing**: Flow where an exit node has an edge to a task node. Assert error with `rule == "S7"`.
9. **S8 — no resolvable cwd**: Flow with `workspace = None` and a node with `cwd = None`. Assert error with `rule == "S8"`.

**Positive tests**:

10. **Valid linear flow**: Parse Appendix A.1 and run `check_flow`. Assert empty error list.
11. **Valid fork-join flow**: Parse Appendix A.2 and run `check_flow`. Assert empty error list.
12. **Valid cycle flow**: Parse Appendix A.3 and run `check_flow`. Assert empty error list.
13. **Valid complex flow**: Parse Appendix A.4 and run `check_flow`. Assert empty error list.
14. **Valid scheduled flow**: Parse Appendix A.5 and run `check_flow`. Assert empty error list.
15. **Valid recurring flow**: Parse Appendix A.6 and run `check_flow`. Assert empty error list.

Note: The positive tests on Appendix flows will only fully pass once DSL-004, DSL-005, and DSL-006 are also implemented. Initially they validate that S1-S8 produce no errors; edge/cycle/fork-join stubs return empty lists.

**Multiple errors**: Test a flow that violates S2 and S8 simultaneously. Assert both errors are returned (the checker should collect all errors, not stop at the first).
