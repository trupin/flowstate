# [DSL-002] Parser (source -> AST)

## Domain
dsl

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: DSL-001
- Blocks: DSL-003, DSL-004, DSL-005, DSL-006

## Spec References
- specs.md Section 3 — "DSL Specification"
- specs.md Section 5.2 — "Parser"
- specs.md Section 11 — "Lark Grammar"
- specs.md Section 11.1 — "AST Node Definitions"
- specs.md Section 12.1 — "Parse Errors"

## Summary
Create the parser module that transforms Flowstate DSL source text into a fully-populated AST (the dataclass tree defined in `ast.py`). This is a Lark Transformer that walks the parse tree produced by the grammar (DSL-001) and builds `Flow`, `Node`, `Edge`, `EdgeConfig`, and `Param` instances. The parser converts DURATION tokens to seconds, handles string escaping, preserves source locations (line/column), and raises `FlowParseError` with descriptive messages on syntax errors.

## Acceptance Criteria
- [ ] `src/flowstate/dsl/parser.py` exists with a `parse_flow(source: str) -> Flow` function
- [ ] `parse_flow` returns a `Flow` AST dataclass with fully populated `nodes`, `edges`, and `params`
- [ ] DURATION tokens are converted to integer seconds: `30s` -> 30, `5m` -> 300, `2h` -> 7200
- [ ] Single-quoted strings have surrounding quotes stripped; triple-quoted strings have surrounding `"""` stripped and leading/trailing whitespace trimmed
- [ ] Line and column info is preserved on `Node` and `Edge` objects (from Lark's `Token.line` and `Token.column`)
- [ ] `FlowParseError` is raised on syntax errors with line, column, and descriptive message
- [ ] `FlowParseError` is defined in `src/flowstate/dsl/exceptions.py` (or `parser.py`)
- [ ] All 6 Appendix A examples parse successfully and produce correct ASTs
- [ ] `nodes` dict is keyed by node name (e.g., `flow.nodes["analyze"]`)
- [ ] Edge types are correctly classified: unconditional, conditional (with `condition` set), fork (with `fork_targets`), join (with `join_sources`)
- [ ] Edge config blocks produce `EdgeConfig` with correct fields (`context`, `delay_seconds`, `schedule`)
- [ ] Parameters with and without defaults are correctly parsed
- [ ] Flow-level attributes (`budget`, `on_error`, `context`, `workspace`, `schedule`, `on_overlap`) are correctly mapped to `Flow` fields
- [ ] Tests exist covering all of the above

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/parser.py` — `parse_flow()` function + Lark Transformer class
- `src/flowstate/dsl/exceptions.py` — `FlowParseError` exception class
- `tests/dsl/test_parser.py` — comprehensive parser tests
- `tests/dsl/fixtures/valid_linear.flow` — Appendix A.1
- `tests/dsl/fixtures/valid_fork_join.flow` — Appendix A.2
- `tests/dsl/fixtures/valid_cycle.flow` — Appendix A.3
- `tests/dsl/fixtures/valid_complex.flow` — Appendix A.4
- `tests/dsl/fixtures/valid_scheduled.flow` — Appendix A.5
- `tests/dsl/fixtures/valid_recurring.flow` — Appendix A.6

### Key Implementation Details

**`FlowParseError` exception**:
```python
class FlowParseError(Exception):
    def __init__(self, message: str, line: int | None = None, column: int | None = None):
        self.line = line
        self.column = column
        loc = ""
        if line is not None:
            loc = f" at line {line}"
            if column is not None:
                loc += f", column {column}"
        super().__init__(f"Parse error{loc}: {message}")
```

**`parse_flow` function**:
```python
from lark import Lark, Transformer, v_args, Token
from lark.exceptions import UnexpectedInput
from pathlib import Path

_GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"
_parser = Lark(
    _GRAMMAR_PATH.read_text(),
    parser="earley",
    propagate_positions=True,  # Critical: enables line/column on Tree nodes
)

def parse_flow(source: str) -> Flow:
    try:
        tree = _parser.parse(source)
    except UnexpectedInput as e:
        raise FlowParseError(str(e), line=e.line, column=e.column) from e
    transformer = FlowTransformer()
    return transformer.transform(tree)
```

Key: `propagate_positions=True` ensures every `Tree` node has `.meta.line` and `.meta.column`.

**`FlowTransformer` class** (extends `lark.Transformer`):

Each method corresponds to a grammar rule and builds the appropriate AST node:

- `flow_decl(items)` -> `Flow`: Extract name, then iterate over flow_body items to collect attributes, params, nodes, edges. Validate that `budget`, `on_error`, and `context` are present (they're required per spec). Raise `FlowParseError` if missing.

- `flow_attr(items)` -> tuple: Return `(attr_name, value)` pairs. The flow_decl method collects these.

- `param_decl(items)` -> `Param`: Build from name, type, optional default.

- `entry_node(items)` / `task_node(items)` / `exit_node(items)` -> `Node`: Extract name and node_body attrs. Set `node_type` accordingly. Capture `meta.line`/`meta.column` from the Tree.

- `node_body(items)` / `node_attr(items)` -> dict: Collect prompt and optional cwd.

- `simple_edge(items)` -> `Edge(edge_type=UNCONDITIONAL, source=..., target=..., config=...)`: Capture line/column.

- `cond_edge(items)` -> `Edge(edge_type=CONDITIONAL, source=..., target=..., condition=..., config=...)`.

- `fork_edge(items)` -> `Edge(edge_type=FORK, source=..., fork_targets=[...])`.

- `join_edge(items)` -> `Edge(edge_type=JOIN, join_sources=[...], target=...)`.

- `edge_config(items)` / `edge_attr(items)` -> `EdgeConfig`: Collect context, delay_seconds, schedule.

- `name_list(items)` -> `list[str]`: Extract names from fork/join target lists.

- `string(items)` -> `str`: Unwrap STRING or LONG_STRING token.

- `literal(items)` -> value: Convert STRING to str, NUMBER to float, true/false to bool.

- `true_lit(items)` -> `True`, `false_lit(items)` -> `False`.

**DURATION conversion**: When processing a `DURATION` token (appears in `flow_attr` for budget and `edge_attr` for delay), parse the integer and suffix:
```python
def _parse_duration(token: str) -> int:
    value, unit = int(token[:-1]), token[-1]
    return value * {"s": 1, "m": 60, "h": 3600}[unit]
```

**String handling**:
- `STRING` token: Strip surrounding `"` characters. The regex `/[^"]*/` means no escape sequences needed for MVP.
- `LONG_STRING` token: Strip surrounding `"""` characters. Do NOT strip internal whitespace beyond the delimiters themselves. The spec examples show triple-quoted prompts with leading whitespace that's part of the content.

**Source location preservation**:
- On `Node` objects: capture from the grammar tree node's `meta.line` and `meta.column` (available because of `propagate_positions=True`).
- On `Edge` objects: same approach.
- If `meta` is not available on a particular tree node (can happen with Earley parser), default to `line=0, column=0`.

**Handling optional fields**:
- `Flow.workspace`: `None` if not declared (some flows use per-task cwd instead).
- `Flow.schedule`: `None` if not declared.
- `Flow.on_overlap`: `OverlapPolicy.SKIP` if not declared (default per spec).
- `Edge.config`: `EdgeConfig()` (all None fields) if no config block present.
- `Node.cwd`: `None` if not declared in the node body.
- `Param.default`: `None` if no default value.

**Flow attribute validation in the parser**:
The parser should verify that required flow attributes are present: `budget`, `on_error`, `context`. If any is missing, raise `FlowParseError` with a descriptive message. This is a syntactic requirement, not a type-checker concern.

### Edge Cases
- Empty prompt string: `prompt = ""`
- Very long prompt spanning many lines in triple-quoted string
- Special characters in strings: backslashes, curly braces (template vars), newlines
- Template variables `{{name}}` in prompts -- these should remain as literal text, not be expanded
- Multiple edges from the same source node (valid for conditional branches)
- Flow with only entry and exit nodes (no task nodes)
- Parameter with bool default: `param verbose: bool = true`
- Parameter with number default: `param retries: number = 3`
- DURATION values: `0s` (zero seconds), single-digit like `5m`, large like `999h`
- Edge config with no attributes: `{}` (empty block)
- Edge config with multiple attributes: `{ context = session delay = 5m }` -- this is invalid per spec (delay and schedule are mutually exclusive with context on same edge), but the parser should still parse it; the type checker enforces E8

## Testing Strategy

Create `tests/dsl/test_parser.py` and fixture files in `tests/dsl/fixtures/`.

**Fixture files**: Copy each Appendix A example (A.1 through A.6) verbatim into `.flow` files. Use a helper function to load fixtures:
```python
from pathlib import Path
FIXTURES = Path(__file__).parent / "fixtures"
def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()
```

**Test cases**:

1. **Parse Appendix A.1 (linear flow)**: Parse `valid_linear.flow`. Assert:
   - `flow.name == "setup_project"`
   - `flow.budget_seconds == 1800` (30m)
   - `flow.on_error == ErrorPolicy.PAUSE`
   - `flow.context == ContextMode.SESSION`
   - `flow.workspace == "./new-project"`
   - 3 nodes: scaffold (entry), add_ci (task), done (exit)
   - 2 edges: both unconditional
   - No params

2. **Parse Appendix A.2 (fork-join flow)**: Parse `valid_fork_join.flow`. Assert:
   - `flow.name == "full_test"`
   - `flow.budget_seconds == 3600` (1h)
   - 5 nodes
   - 1 fork edge with 3 targets: `["test_unit", "test_integration", "test_e2e"]`
   - 1 join edge with 3 sources

3. **Parse Appendix A.3 (cycle flow)**: Parse `valid_cycle.flow`. Assert:
   - `flow.name == "iterative_refactor"`
   - 1 param: `target` of type `string` with no default
   - 4 nodes
   - 4 edges: 2 unconditional, 2 conditional
   - Conditional edges have `condition` set to the when-clause text

4. **Parse Appendix A.4 (fork-join with cycle)**: Parse `valid_complex.flow`. Assert:
   - `flow.name == "feature_development"`
   - `flow.workspace is None` (no flow-level workspace)
   - All nodes have `cwd` set
   - 1 param: `feature` of type `string`
   - Fork and join edges present
   - Conditional edges present (review -> ship, review -> design)

5. **Parse Appendix A.5 (scheduled deployment)**: Parse `valid_scheduled.flow`. Assert:
   - Edge from prepare to deploy has `config.schedule == "0 2 * * *"`
   - Edge from deploy to check_health has `config.delay_seconds == 300` (5m)
   - Self-loop edge check_health -> check_health has `config.delay_seconds == 120` (2m)
   - Conditional edges on check_health

6. **Parse Appendix A.6 (recurring weekly)**: Parse `valid_recurring.flow`. Assert:
   - `flow.schedule == "0 9 * * MON"`
   - `flow.on_overlap == OverlapPolicy.SKIP`

7. **Syntax error -- missing arrow**: Parse `"flow f { entry a { prompt = \"x\" } task b { prompt = \"y\" } a b }"`. Assert `FlowParseError` is raised with line info.

8. **Syntax error -- unclosed brace**: Parse a flow with missing closing `}`. Assert `FlowParseError`.

9. **Syntax error -- invalid keyword**: Parse a flow with `invalid_keyword = value`. Assert `FlowParseError`.

10. **Edge config parsing**: Parse an edge with `{ context = session }` and verify `edge.config.context == ContextMode.SESSION`.

11. **Parameter with default**: Parse `param retries: number = 3`. Verify `param.default == 3.0` (or 3) and `param.type == ParamType.NUMBER`.

12. **Parameter without default**: Parse `param name: string`. Verify `param.default is None`.

13. **DURATION conversion**: Test that `30s` -> 30, `5m` -> 300, `2h` -> 7200, `0s` -> 0.

14. **String escaping**: Parse prompts with template variables `{{name}}` and verify they appear as literal text in the AST.

15. **Line/column info**: Parse a multi-line flow and verify that nodes and edges have non-zero `line` values that correspond to their position in the source.

16. **Missing required attribute**: Parse a flow without `budget`. Assert `FlowParseError` with a message mentioning "budget".
