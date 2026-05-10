# [DSL-015] Add `agent` node attribute for reusable persona references

## Domain
dsl

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: ENGINE-086

## Spec References
- specs.md Section 3.4 — "Node Declarations" (new `agent` attribute)
- specs.md Section 11.1 — "AST" (`Node.agent` field)

## Summary
Add an `agent` string attribute to node declarations. The value names a persona file (e.g. `agent = "helly"`) that the engine resolves to an `agent.md` file using Claude Code's existing precedence: `<flow_dir>/agents/<name>.md`, then `~/.claude/agents/<name>.md`. The DSL layer adds the field, parses it, and validates that the file exists at parse time so typos surface as type errors instead of run-time failures. The engine wires the resolved persona into the subprocess as a system prompt (ENGINE-086).

## Acceptance Criteria
- [ ] `agent = "<name>"` parses at node level (entry, task, exit, atomic) — default: None
- [ ] AST `Node` dataclass has `agent: str | None = None`
- [ ] Type checker rule AG1: when `agent` is set, the resolved `agent.md` file must exist in either `<flow_dir>/agents/<name>.md` or `~/.claude/agents/<name>.md`. Missing file → error referencing both lookup paths
- [ ] Type checker rule AG2: when `agent` is set, the resolved file's YAML frontmatter must parse (if present). Malformed frontmatter → error
- [ ] All existing tests still pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/dsl/ast.py` — add `agent: str | None = None` to `Node`
- `src/flowstate/dsl/grammar.lark` — add `node_agent` rule
- `src/flowstate/dsl/parser.py` — add transformer method, thread field through node builders
- `src/flowstate/dsl/type_checker.py` — add AG1 + AG2 validation rules
- `src/flowstate/dsl/exceptions.py` — no new exception type needed; reuse `FlowTypeError`
- `tests/dsl/fixtures/valid_agent.flow` — fixture with `agent = "demo"` (and a `agents/demo.md` next to it)
- `tests/dsl/fixtures/agents/demo.md` — minimal persona file with frontmatter
- `tests/dsl/fixtures/invalid_agent_missing.flow` — references nonexistent persona
- `tests/dsl/test_parser.py` — parser tests
- `tests/dsl/test_type_checker.py` — AG1 / AG2 tests

### Key Implementation Details

**AST (`ast.py`):**
Add to `Node` (alongside other optional string fields like `harness`, `sandbox_policy`):
```python
agent: str | None = None
```

**Grammar (`grammar.lark`):**
Add to `node_attr` (mirrors `node_harness`):
```lark
| "agent" "=" STRING -> node_agent
```

**Parser (`parser.py`):**
Transformer method:
```python
def node_agent(self, items):
    return ("agent", _strip_string(items[0]))
```
Update each node builder (`entry_node`, `task_node`, `exit_node`, `atomic_node`) to extract `agent` from the attrs dict and pass to `Node(...)`.

**Type Checker (`type_checker.py`):**
Add AG1 + AG2. The type checker needs the `flow_file_dir` to resolve `<flow_dir>/agents/<name>.md`. The existing type checker entry point is called with the parsed `Flow` and (per existing convention) the source path. Pass `flow_file_dir` through to the new check.

```python
def _check_agent_files(flow: Flow, flow_file_dir: Path | None) -> list[FlowTypeError]:
    errors: list[FlowTypeError] = []
    for node in flow.nodes.values():
        if node.agent is None:
            continue
        path = _resolve_agent_md(node.agent, flow_file_dir)
        if path is None:
            errors.append(FlowTypeError(
                f"AG1: agent '{node.agent}' on node '{node.name}' not found "
                f"(looked in <flow_dir>/agents/{node.agent}.md and "
                f"~/.claude/agents/{node.agent}.md)"
            ))
            continue
        # AG2: parse frontmatter if present
        try:
            _parse_agent_frontmatter(path)
        except Exception as e:
            errors.append(FlowTypeError(
                f"AG2: agent '{node.agent}' on node '{node.name}' has malformed "
                f"frontmatter at {path}: {e}"
            ))
    return errors
```

`_resolve_agent_md(name, flow_dir)` checks `<flow_dir>/agents/<name>.md` first, then `~/.claude/agents/<name>.md`. Returns `Path | None`.

`_parse_agent_frontmatter(path)` reads the file. If the file starts with `---\n`, parses the YAML block between the first two `---` delimiters. Otherwise no-op (frontmatter is optional). Use `yaml.safe_load`. Add `pyyaml` to project dependencies if not already present.

### Edge Cases
- `agent = ""` (empty string) → AG1 error
- `agent` references a path with directory separators (`agent = "foo/bar"`) → reject with clear error; only bare names allowed
- File exists but is empty → valid (no frontmatter, no body — engine will still load empty system_prompt)
- Frontmatter present but no body → valid
- File has `---` markers but no terminating `---` → AG2 error (malformed)
- `flow_file_dir` is None (e.g. parsing from a string with no path) → only check `~/.claude/agents/`; emit a warning if neither lookup is possible

## Testing Strategy
- Parser tests: verify `agent` parses at all node types
- Fixture: a `.flow` file with `agent = "demo"` and a sibling `agents/demo.md` parses and type-checks successfully
- AG1 test: `.flow` file with `agent = "missing"` and no `agents/missing.md` → error
- AG2 test: `agent.md` with malformed YAML frontmatter (e.g. `---\nname: [unterminated\n---\n`) → error
- Frontmatter-less agent.md → valid

## E2E Verification Plan

### Verification Steps
1. Create `flows/test_agent.flow` with a node `task helly { agent = "helly" prompt = "..." }`
2. Create `flows/agents/helly.md`:
   ```markdown
   ---
   name: Helly R.
   model: claude-opus-4-7
   description: Stress-tester. Pushes back on flimsy reasoning.
   ---
   
   You are Helly R., a relentless challenger...
   ```
3. Run `/check flows/test_agent.flow` → should pass
4. Delete `flows/agents/helly.md`, re-run `/check` → should report AG1 error citing both lookup paths
5. Recreate with malformed frontmatter (`---\n: [\n---`), re-run `/check` → should report AG2 error

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in: exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
