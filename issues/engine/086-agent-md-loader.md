# [ENGINE-086] Resolve `agent.md` and wire as subprocess system prompt

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: DSL-015
- Blocks: —

## Spec References
- specs.md Section 6.2 — "Task Execution Lifecycle" (system prompt source)
- specs.md Section 11.1 — "AST" (`Node.agent`)

## Summary
When a node has `agent = "<name>"` set, the engine resolves the persona file (per Claude Code's precedence: `<flow_dir>/agents/<name>.md`, then `~/.claude/agents/<name>.md`), parses optional YAML frontmatter, and uses the file body as the subprocess system prompt via `subprocess_mgr.run_task_with_system_prompt`. The node's `prompt` field becomes the kickoff message. Frontmatter `model` is honored when present (selects a different harness or passes through to the harness). Other frontmatter fields (`name`, `description`, `tools`) are read but not acted on in this issue — they're recorded in logs for observability and reserved for future per-node tool restriction.

## Acceptance Criteria
- [ ] When `Node.agent` is set, the engine reads the resolved `agent.md` and uses its body (post-frontmatter) as the subprocess system prompt
- [ ] Node's `prompt` field is sent as the user-facing kickoff message (separate from system prompt)
- [ ] `{{template_var}}` substitution applies to **both** system prompt (agent.md body) and kickoff message (node.prompt) using the same param dict
- [ ] Frontmatter `model: <id>` is honored: if the value matches a registered harness name, that harness is selected for the node; otherwise emit a warning and fall back to the node's existing `harness` resolution
- [ ] Run-time error if the resolved file disappeared between type-check and execution (defense in depth) — task fails with a clear message
- [ ] Existing nodes without `agent` set behave exactly as before (no regression)
- [ ] When both `agent` and an inline `prompt` are set, both are used (system + kickoff). When only `agent` is set, kickoff is `"Begin."` (or similar minimal trigger)

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/context.py` — new helper `load_agent_persona(node, flow_file_dir, params) -> AgentPersona | None`
- `src/flowstate/engine/executor.py` — wherever the prompt is constructed and dispatched, branch on `node.agent` to call the system-prompt path
- `src/flowstate/engine/subprocess_mgr.py` — already has `run_task_with_system_prompt`; ensure it's reachable through the harness protocol path (today only the judge uses it)
- `src/flowstate/engine/harness.py` — verify the protocol exposes a system-prompt variant; if not, add one and implement it on each backend
- `tests/engine/test_agent_persona.py` — new test file
- `tests/engine/fixtures/agents/sample.md` — fixture persona

### Key Implementation Details

**Persona loader (`context.py`):**
```python
@dataclass(frozen=True)
class AgentPersona:
    system_prompt: str
    model: str | None
    raw_frontmatter: dict[str, Any]
    source_path: Path

def load_agent_persona(
    node: Node, flow_file_dir: Path | None, params: dict[str, str | float | bool]
) -> AgentPersona | None:
    if node.agent is None:
        return None
    path = _resolve_agent_md(node.agent, flow_file_dir)
    if path is None:
        raise AgentPersonaError(
            f"agent '{node.agent}' on node '{node.name}' not found "
            f"(looked in <flow_dir>/agents/ and ~/.claude/agents/)"
        )
    text = path.read_text()
    frontmatter, body = _split_frontmatter(text)
    body = expand_templates(body, params)
    return AgentPersona(
        system_prompt=body,
        model=frontmatter.get("model"),
        raw_frontmatter=frontmatter,
        source_path=path,
    )
```

Reuse `_resolve_agent_md` from DSL-015 (move to a shared `dsl.agent_resolver` module if convenient, or duplicate the small function — both packages can read it).

`_split_frontmatter(text)` — if text starts with `---\n`, find next `---\n`, parse the YAML between them via `yaml.safe_load`. Otherwise return `({}, text)`.

**Executor wiring:**

Today the executor builds prompts via `build_prompt_handoff` / `build_prompt_session` / `build_prompt_none` / `build_prompt_join` and dispatches via `harness.run_task(prompt, ...)`. When `node.agent` is set:

1. Call `load_agent_persona(node, flow_file_dir, params)` → `AgentPersona`
2. The system_prompt is the persona body
3. The kickoff message is the existing `build_prompt_*` output (which contains predecessor context, directory sections, etc.)
4. Dispatch via `harness.run_task_with_system_prompt(system_prompt=persona.system_prompt, init_message=kickoff, workspace=cwd, session_id=...)`

If the harness protocol does not currently expose `run_task_with_system_prompt`, add it. Look at `subprocess_mgr.py:104-141` for the existing implementation. The ACP and SDK harnesses need parallel implementations — for now, if only `SubprocessManager` supports it, raise `NotImplementedError` with a clear message when the active harness for an `agent`-using node doesn't support system prompts. Don't silently fall back.

**Model resolution from frontmatter:**

```python
if persona.model:
    try:
        active_harness = harness_manager.get(persona.model)
    except HarnessNotFoundError:
        logger.warning(
            "agent.md frontmatter requested model %r but no matching harness "
            "is registered; falling back to node/flow harness", persona.model
        )
        active_harness = harness_manager.get(node.harness or flow.harness)
else:
    active_harness = harness_manager.get(node.harness or flow.harness)
```

Note: the `model` field semantically maps to a *harness name*, since harnesses are how Flowstate selects backends. If users want to map specific Anthropic model IDs (`claude-opus-4-7`), that requires the harness manager to register such names — out of scope here. Document the mapping in spec.

### Edge Cases
- File deleted between type-check and execution → raise `AgentPersonaError`, fail the task (don't silently skip)
- Empty body (frontmatter only) → empty system prompt; warn but proceed
- Body contains template vars referencing params not in scope → unmatched vars left as-is (matches `expand_templates` semantics)
- Frontmatter contains `tools: [...]` — read into `raw_frontmatter` for logs but **do not** act on it (out of scope; future tool-restriction work)
- `agent` set on a node that is also using `context = session` resume → the system prompt is set on **session start** only; subsequent prompts in the same session don't re-set it. Document this; for advisor flows, `handoff` mode is the natural choice anyway

## Testing Strategy
- Unit test: `load_agent_persona` with a fixture file containing frontmatter + body — verify both parsed correctly, templates expanded
- Unit test: persona file missing → `AgentPersonaError`
- Unit test: malformed frontmatter at run-time → `AgentPersonaError`
- Integration test (mocked harness): node with `agent` dispatches to `run_task_with_system_prompt` with the right system prompt and kickoff message
- Integration test: frontmatter `model: <unknown>` → warning logged, fallback to node/flow harness
- Regression: nodes without `agent` use the existing dispatch path unchanged

## E2E Verification Plan

### Verification Steps
1. Create `flows/test_agent.flow` with `task helly { agent = "helly" prompt = "Question: {{topic}}" }` and a fork-join council
2. Create `flows/agents/helly.md` with frontmatter (`name`, `description`) and a strong persona body
3. Submit a task with `topic = "should I refactor X"`
4. Inspect the subprocess command line / logs: confirm `--system-prompt` is the helly.md body and `-p` is the templated kickoff
5. Inspect the run output: helly's response should reflect the persona (challenging tone, etc.)
6. Delete the file, re-run: task should fail with a clear `AgentPersonaError`

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in: exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
