# Agent 1: DSL — Parser + Type Checker

## Role

You are implementing the Flowstate DSL layer: the Lark grammar, the parser that transforms source text into an AST, and the type checker that validates the AST against all static analysis rules.

Read `specs.md` sections **3 (DSL Specification)**, **4 (Type System and Static Analysis)**, and **11 (Lark Grammar)** for the full requirements.

## Phase

**Phase 1** — no dependencies on other agents. You need `ast.py` (the shared AST dataclasses). If it doesn't exist yet, create it as defined in specs.md Section 11.1.

## Files to Create

```
src/flowstate/__init__.py
src/flowstate/dsl/__init__.py
src/flowstate/dsl/ast.py              ← shared AST dataclasses (Section 11.1)
src/flowstate/dsl/grammar.lark        ← Lark grammar (Section 11)
src/flowstate/dsl/parser.py           ← Lark transformer: source text → AST
src/flowstate/dsl/type_checker.py     ← static analysis: AST → validated AST or errors
tests/__init__.py
tests/dsl/__init__.py
tests/dsl/test_parser.py
tests/dsl/test_type_checker.py
tests/dsl/fixtures/                   ← .flow fixture files
```

## Dependencies

- **Python packages:** `lark` (parser generator)
- **Internal:** None. This is a leaf module — it has no imports from other flowstate packages.

## Exported Interface

Other agents import from this module:

```python
from flowstate.dsl.ast import (
    Flow, Node, Edge, EdgeConfig, Param,
    NodeType, EdgeType, ContextMode, ErrorPolicy, ParamType,
)
from flowstate.dsl.parser import parse_flow         # str → Flow (raises ParseError)
from flowstate.dsl.type_checker import check_flow   # Flow → list[TypeError] (empty = valid)
```

### `parse_flow(source: str) -> Flow`
- Parses DSL source text into a `Flow` AST
- Raises `FlowParseError` with line/column on syntax errors
- Does NOT validate semantics — that's the type checker's job

### `check_flow(flow: Flow) -> list[FlowTypeError]`
- Validates all 17 rules from Section 4 of the spec
- Returns empty list if valid
- Each error includes: rule ID (e.g., "S1"), message, and source location (node/edge name)

## AST Definitions

Create `ast.py` exactly as specified in Section 11.1 of specs.md. These dataclasses are the shared contract between all agents. **Do not modify the field names or types** — other agents depend on them.

Key types:
- `Flow`: top-level container (name, budget_seconds, workspace, on_error, **context**, params, nodes, edges)
- `Node`: graph vertex (name, node_type, prompt, line, column)
- `Edge`: graph edge (edge_type, source, target, fork_targets, join_sources, condition, config)
- `EdgeConfig`: per-edge configuration (context mode — `None` means "use flow default")
- Enums: `NodeType`, `EdgeType`, `ContextMode` (`HANDOFF`, `SESSION`, `NONE`), `ErrorPolicy`, `ParamType`

## Lark Grammar

Implement the grammar from Section 11 of specs.md. Key considerations:

- Use **Earley** parser (handles ambiguity gracefully, better error messages)
- STRING and LONG_STRING need careful regex (triple-quoted strings containing quotes)
- COMMENT terminal: `//` style (Lark's built-in `SH_COMMENT` uses `#` — define a custom terminal)
- DURATION: `[0-9]+[smh]` — convert to seconds in the transformer
- Template variables `{{name}}` in prompts are **not** parsed by Lark — they're just part of string content, expanded at runtime by the execution engine

## Type Checker Rules

Implement ALL rules from Section 4:

**Structural (S1-S7):**
- S1: Exactly one entry node
- S2: At least one exit node
- S3: All nodes reachable from entry (BFS/DFS)
- S4: At least one exit reachable from every node
- S5: No duplicate node names
- S6: Entry has no incoming edges
- S7: Exit nodes have no outgoing edges

**Edge (E1-E7):**
- E1: Node with 1 outgoing edge must be unconditional
- E2: Node with 2+ outgoing: all conditional OR a single fork
- E3: No mixing fork and conditional from same node
- E4: All edge references point to existing nodes
- E5: Fork target set must match exactly one join's source set
- E6: Join source set must match exactly one fork's target set
- E7: `context = session` not allowed on fork or join edges

**Cycle (C1-C3):**
- C1: Cycle targets must be outside fork-join groups
- C2: Every cycle must pass through at least one conditional edge
- C3: Flows with cycles must declare a budget

**Fork-Join (F1-F3):**
- F1: Fork groups may nest but must not partially overlap
- F2: Join node cannot also be fork source in same declaration
- F3: Fork targets must eventually converge to a single join

## Testing Requirements

### Parser tests (`test_parser.py`)
- Parse all 4 example flows from Appendix A of specs.md
- Verify AST structure matches expected (node count, edge types, params, etc.)
- Test syntax error reporting: missing arrows, unclosed braces, invalid keywords
- Test edge cases: empty prompts, very long prompts, special characters in strings
- Test all edge types: unconditional, conditional, fork, join, with config blocks
- Test parameter declarations with and without defaults

### Type checker tests (`test_type_checker.py`)
- One test per rule (S1-S7, E1-E7, C1-C3, F1-F3) = 17 negative tests
- Each test provides a deliberately invalid flow and asserts the correct error is returned
- Also test valid flows return no errors (all 4 Appendix A examples must pass)
- Test nested fork-join validity
- Test that cycle detection correctly identifies back-edges

### Fixture files
Create `.flow` files in `tests/dsl/fixtures/` for reuse:
- `valid_linear.flow` (Appendix A.1)
- `valid_fork_join.flow` (Appendix A.2)
- `valid_cycle.flow` (Appendix A.3)
- `valid_complex.flow` (Appendix A.4)
- `invalid_no_entry.flow`
- `invalid_mixed_edges.flow`
- `invalid_cycle_in_fork.flow`
- etc.

## Key Constraints

1. **Do not import from other flowstate packages.** This module is self-contained.
2. **`ast.py` is a shared contract.** If you need to change it, document why — other agents depend on it.
3. **Error messages must include source locations** (line/column from the Lark parse tree, or node/edge names for type errors).
4. **Template variables (`{{name}}`) are NOT expanded by the parser.** They remain as literal text in the prompt string. Validation that param names match template variables is a nice-to-have but not required.
5. **Use `pytest` for all tests.** No unittest, no nose.
