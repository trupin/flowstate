# [SHARED-001] AST Definitions (shared contract)

## Domain
shared

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: none
- Blocks: DSL-001, DSL-002, STATE-001, ENGINE-001, ENGINE-003

## Spec References
- specs.md Section 11.1 — "AST Node Definitions"
- agents/01-dsl.md — "AST Definitions" section

## Summary
Define the shared AST data model in `src/flowstate/dsl/ast.py`. This module is the single source of truth for all Python packages in the project — DSL, state, engine, and server all import from it. It contains frozen dataclasses (`Flow`, `Node`, `Edge`, `EdgeConfig`, `Param`) and string enums (`NodeType`, `EdgeType`, `ContextMode`, `ErrorPolicy`, `ParamType`, `OverlapPolicy`) that represent a parsed `.flow` file. Because every other domain depends on these definitions, they must exactly match the spec and must never import from any other flowstate package.

## Acceptance Criteria
- [ ] File `src/flowstate/dsl/ast.py` exists and is importable as `from flowstate.dsl.ast import ...`
- [ ] All 6 enums are defined as `str, Enum` (string enums so they serialize cleanly to JSON):
  - `NodeType` with values: `ENTRY = "entry"`, `TASK = "task"`, `EXIT = "exit"`
  - `EdgeType` with values: `UNCONDITIONAL = "unconditional"`, `CONDITIONAL = "conditional"`, `FORK = "fork"`, `JOIN = "join"`
  - `ContextMode` with values: `HANDOFF = "handoff"`, `SESSION = "session"`, `NONE = "none"`
  - `ErrorPolicy` with values: `PAUSE = "pause"`, `ABORT = "abort"`, `SKIP = "skip"`
  - `ParamType` with values: `STRING = "string"`, `NUMBER = "number"`, `BOOL = "bool"`
  - `OverlapPolicy` with values: `SKIP = "skip"`, `QUEUE = "queue"`, `PARALLEL = "parallel"`
- [ ] All 5 dataclasses are defined as frozen (`@dataclass(frozen=True)`) with fields matching specs.md Section 11.1 exactly:
  - `Param(name: str, type: ParamType, default: str | float | bool | None = None)`
  - `Node(name: str, node_type: NodeType, prompt: str, cwd: str | None = None, line: int = 0, column: int = 0)`
  - `EdgeConfig(context: ContextMode | None = None, delay_seconds: int | None = None, schedule: str | None = None)`
  - `Edge(edge_type: EdgeType, source: str | None = None, target: str | None = None, fork_targets: tuple[str, ...] | None = None, join_sources: tuple[str, ...] | None = None, condition: str | None = None, config: EdgeConfig = <default EdgeConfig>, line: int = 0, column: int = 0)`
  - `Flow(name: str, budget_seconds: int, on_error: ErrorPolicy, context: ContextMode, workspace: str | None = None, schedule: str | None = None, on_overlap: OverlapPolicy = OverlapPolicy.SKIP, params: tuple[Param, ...] = (), nodes: dict[str, Node] = <empty>, edges: tuple[Edge, ...] = ())`
- [ ] No imports from any other `flowstate` package (dsl is a leaf module)
- [ ] `__init__.py` files exist for `src/flowstate/` and `src/flowstate/dsl/`
- [ ] The `dsl/__init__.py` re-exports nothing (or re-exports the AST types — either is fine, but the canonical import path is `flowstate.dsl.ast`)
- [ ] All types pass `pyright` in standard mode with no errors
- [ ] All tests in `tests/dsl/test_ast.py` pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/__init__.py` — empty package init (or minimal version string)
- `src/flowstate/dsl/__init__.py` — empty package init
- `src/flowstate/dsl/ast.py` — all AST definitions
- `tests/__init__.py` — empty
- `tests/dsl/__init__.py` — empty
- `tests/dsl/test_ast.py` — AST definition tests

### Key Implementation Details

#### Enums
Use `class NodeType(str, Enum)` pattern (not `StrEnum` from 3.11+, for clarity). String enums ensure that `NodeType.ENTRY == "entry"` and JSON serialization works naturally.

```python
from enum import Enum

class NodeType(str, Enum):
    ENTRY = "entry"
    TASK = "task"
    EXIT = "exit"
```

Repeat for all 6 enums with values exactly as listed in acceptance criteria above.

#### Dataclasses
Use `@dataclass(frozen=True)` for immutability. The AST should be an immutable value once constructed.

**Important difference from the spec code block:** The spec shows `list[str]` for `fork_targets` and `join_sources`, and `list[Param]` / `list[Edge]` for collection fields. Since the dataclasses are frozen, use `tuple[str, ...]` and `tuple[Param, ...]` / `tuple[Edge, ...]` instead of lists — frozen dataclasses with mutable default fields cause issues, and tuples are the idiomatic immutable sequence. For `Flow.nodes`, keep `dict[str, Node]` but use `field(default_factory=dict)` — the frozenness prevents reassigning the attribute, but the dict itself is mutable (this is an accepted trade-off for lookup performance; the type checker and engine need O(1) node lookup by name).

For `Edge.config`, use `field(default_factory=EdgeConfig)` to provide a default empty config.

#### No `__all__` required
All public names are the classes and enums themselves. No private helpers.

#### Import structure
Only standard library imports: `dataclasses`, `enum`. No third-party dependencies.

### Edge Cases
- `Edge` has mutually exclusive field groups: `source`/`target` (for unconditional/conditional), `fork_targets` (for fork), `join_sources` (for join). The AST does not enforce this — the type checker validates it later. Do NOT add runtime validation to the dataclasses.
- `EdgeConfig.context = None` means "inherit the flow-level default". This is distinct from `ContextMode.NONE`.
- `Flow.budget_seconds` is an `int`, not `Optional[int]`. The parser must always provide a value (the grammar requires it for most flows; the type checker enforces it for flows with cycles via rule C3).
- Frozen dataclasses do not allow field mutation after construction. Callers must use `dataclasses.replace()` to create modified copies.
- `Flow.nodes` is a `dict[str, Node]` where the key is the node name. The parser is responsible for populating this correctly. The type checker validates uniqueness (rule S5).

## Testing Strategy

Create `tests/dsl/test_ast.py` with the following tests:

1. **test_enum_values** — Verify each enum member has the expected string value (e.g., `assert NodeType.ENTRY.value == "entry"`). Cover all 6 enums.

2. **test_enum_string_behavior** — Verify string enum behavior: `assert NodeType.ENTRY == "entry"`, `assert str(NodeType.ENTRY) == "entry"` or similar.

3. **test_param_creation** — Create a `Param` with all fields, verify attributes. Create one with default=None (no default).

4. **test_node_creation** — Create entry, task, and exit nodes. Verify all fields including defaults (line=0, column=0, cwd=None).

5. **test_edge_config_defaults** — Create `EdgeConfig()` with no args, verify all fields are None.

6. **test_edge_creation** — Create one of each edge type (unconditional, conditional, fork, join). Verify the correct fields are populated and others are None.

7. **test_flow_creation** — Create a minimal `Flow` with required fields only, verify defaults (params=(), nodes={}, edges=(), schedule=None, on_overlap=OverlapPolicy.SKIP).

8. **test_frozen_dataclasses** — Attempt to mutate a field on each dataclass, assert `FrozenInstanceError` is raised.

9. **test_dataclass_replace** — Use `dataclasses.replace()` to create a modified copy of a Node, verify the original is unchanged and the copy has the new value.

10. **test_all_types_importable** — `from flowstate.dsl.ast import Flow, Node, Edge, EdgeConfig, Param, NodeType, EdgeType, ContextMode, ErrorPolicy, ParamType, OverlapPolicy` succeeds without error.
