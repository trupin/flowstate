# [DSL-001] Lark Grammar Definition

## Domain
dsl

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-001
- Blocks: DSL-002

## Spec References
- specs.md Section 3 — "DSL Specification"
- specs.md Section 11 — "Lark Grammar"

## Summary
Create the Lark grammar file that defines the complete Flowstate DSL syntax. This grammar is the foundation for all parsing -- it must handle flow declarations, parameters, all three node types (entry, task, exit), all four edge types (unconditional, conditional, fork, join), edge configuration blocks (context, delay, schedule), comments, string literals (single-quoted and triple-quoted), DURATION tokens, and template variables. The grammar uses the Earley parser for better ambiguity handling and error messages.

## Acceptance Criteria
- [ ] `src/flowstate/dsl/grammar.lark` exists and is a valid Lark grammar file
- [ ] Grammar loads without Lark errors: `Lark(grammar, parser="earley")` succeeds
- [ ] Grammar can parse all 6 Appendix A examples from specs.md (A.1 through A.6)
- [ ] Grammar handles single-line `// comments`
- [ ] Grammar handles both `"double quoted"` and `"""triple-quoted multiline"""` strings
- [ ] Grammar handles DURATION tokens: `30s`, `5m`, `2h`
- [ ] Grammar handles template variables inside strings (e.g., `{{focus}}`) as literal string content (not parsed separately)
- [ ] Grammar handles edge config blocks: `{ context = handoff }`, `{ delay = 5m }`, `{ schedule = "0 2 * * *" }`
- [ ] Grammar handles `on_overlap` flow attribute with values `skip`, `queue`, `parallel`
- [ ] Grammar handles `schedule` flow attribute (cron expression as string)
- [ ] Test file exists that verifies the grammar loads and can parse basic input

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/grammar.lark` — the complete Lark grammar
- `tests/dsl/test_grammar.py` — basic tests that the grammar loads and parses without errors

### Key Implementation Details

The grammar must be written exactly according to specs.md Section 11, with these considerations:

**Parser choice**: Use `parser="earley"`. Earley handles ambiguity gracefully and produces better error messages than LALR. Configure in `parser.py` (DSL-002), but the grammar must be compatible.

**Top-level structure**:
```lark
start: flow_decl
flow_decl: "flow" NAME "{" flow_body "}"
flow_body: (flow_stmt)*
flow_stmt: flow_attr | param_decl | node_decl | edge_decl
```

**Flow attributes** (6 kinds):
```lark
flow_attr: "budget" "=" DURATION
         | "workspace" "=" STRING
         | "on_error" "=" ERROR_POLICY
         | "context" "=" CONTEXT_MODE
         | "schedule" "=" STRING
         | "on_overlap" "=" OVERLAP_POLICY

ERROR_POLICY: "pause" | "abort" | "skip"
OVERLAP_POLICY: "skip" | "queue" | "parallel"
```

**Parameters**:
```lark
param_decl: "param" NAME ":" TYPE
          | "param" NAME ":" TYPE "=" literal
TYPE: "string" | "number" | "bool"
literal: STRING | NUMBER | "true" -> true_lit | "false" -> false_lit
```

**Nodes** (3 types):
```lark
node_decl: entry_node | task_node | exit_node
entry_node: "entry" NAME "{" node_body "}"
task_node:  "task"  NAME "{" node_body "}"
exit_node:  "exit"  NAME "{" node_body "}"
node_body: (node_attr)+
node_attr: "prompt" "=" string | "cwd" "=" STRING
```

**Edges** (4 types + config):
```lark
edge_decl: simple_edge | cond_edge | fork_edge | join_edge
simple_edge: NAME "->" NAME [edge_config]
cond_edge:   NAME "->" NAME "when" string [edge_config]
fork_edge:   NAME "->" "[" name_list "]"
join_edge:   "[" name_list "]" "->" NAME
name_list: NAME ("," NAME)*
edge_config: "{" edge_attr* "}"
edge_attr: "context" "=" CONTEXT_MODE
         | "delay" "=" DURATION
         | "schedule" "=" STRING
```

**String handling**:
- `STRING`: Double-quoted strings — `"\"" /[^"]*/ "\""`
- `LONG_STRING`: Triple-quoted multiline strings — `"\"\"\"" /[\s\S]*?/ "\"\"\"` (lazy match to find the first closing `"""`)
- The `string` rule combines both: `string: STRING | LONG_STRING`
- Template variables `{{name}}` inside strings are NOT parsed by the grammar. They remain as literal string content.

**Token definitions**:
- `DURATION`: `/[0-9]+[smh]/` — matches `30s`, `5m`, `2h`
- `NAME`: `/[a-zA-Z_][a-zA-Z0-9_]*/` — identifiers
- `NUMBER`: `/[0-9]+(\.[0-9]+)?/` — integer or decimal
- `COMMENT`: `/\/\/[^\n]*/` — single-line `//` comments (NOT Lark's built-in `SH_COMMENT` which uses `#`)
- `CONTEXT_MODE`: `"handoff" | "session" | "none"`

**Whitespace and comments**:
```lark
%import common.WS
%ignore WS
%ignore COMMENT
```

**Critical: keyword/identifier ambiguity**. The keywords `pause`, `abort`, `skip`, `handoff`, `session`, `none`, `queue`, `parallel`, `string`, `number`, `bool`, `true`, `false` must NOT collide with the `NAME` terminal. Lark's Earley parser handles this via terminal priority, but verify that `ERROR_POLICY`, `OVERLAP_POLICY`, `CONTEXT_MODE`, and `TYPE` terminals take priority over `NAME` in their respective contexts. If collisions occur, use Lark's terminal priority syntax (e.g., define these as higher-priority terminals) or restructure as rules instead of terminals.

### Edge Cases
- Triple-quoted strings containing double-quote characters (e.g., `"""She said "hello"."""`)
- Empty triple-quoted strings (`""""""`)
- DURATION values at boundaries: `0s`, `999h`
- Edge config blocks with no attributes: `{}` (should be allowed by `edge_attr*`)
- Multiple flow attributes in any order
- Flow with no parameters
- Flow with no workspace (per-task cwd only, as in Appendix A.4)
- Comments at end of lines with code
- Comments on their own lines
- `schedule` on both flow-level (recurring) and edge-level (wait for cron)

## Testing Strategy

Create `tests/dsl/test_grammar.py`:

1. **Grammar loads**: Import the grammar file and create a Lark instance with `parser="earley"`. Assert no exception.
2. **Parse minimal flow**: Parse a minimal valid flow (1 entry, 1 exit, 1 edge). Assert a parse tree is returned.
3. **Parse Appendix A examples**: Parse all 6 example flows from specs.md Appendix A (A.1-A.6). Assert each returns a parse tree without errors. Store these as fixture strings or files in `tests/dsl/fixtures/`.
4. **Parse edge config blocks**: Parse edges with `context`, `delay`, and `schedule` config blocks.
5. **Syntax errors**: Verify that clearly invalid input (e.g., missing braces, missing `->`) raises `lark.exceptions.UnexpectedInput` or similar.
