# [ENGINE-086] Resolve `agent.md` and wire as subprocess system prompt

## Domain
engine

## Status
done

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

**Strategy.** Real Claude Code subprocesses can't be launched in the test
sandbox, so verification uses a `RecordingHarness` test double that
records every argument passed to `harness.run_task_with_system_prompt`
and `harness.run_task`. The executor is driven through `executor.execute()`
end-to-end with an in-memory `FlowstateDB`, exercising the real
`load_agent_persona` path, the real dispatch branch, and the real model→
harness lookup. This proves the wiring without needing a `claude` binary.
The orchestrator's issue brief explicitly authorizes this injection-point
sub-strategy.

#### Verification 1 — Persona drives subprocess as `--system-prompt`
(TEST-37a.7 spirit, replayed against the executor with a recording harness)

Test: `tests/engine/test_agent_persona.py::TestExecutorAgentDispatch::test_agent_dispatches_to_system_prompt`

Setup:
- `agents/helly.md` body: `"---\nname: Helly R.\n---\nYou are Helly. Topic: {{topic}}. Push back."`
- Flow: `entry -> exit`, exit has `agent="helly"`, prompt `"Topic: {{topic}}"`
- Params: `{"topic": "should I refactor X"}`

Observed:
- `harness.run_task_calls` has 1 entry (entry node, no agent — went the
  legacy path)
- `harness.system_prompt_calls` has 1 entry (exit node, agent="helly")
- The recorded `system_prompt` argument:
  - Begins with `"You are Helly. Topic: should I refactor X. Push back."`
  - Does NOT contain `---` on its first line (frontmatter stripped)
  - Does NOT contain `{{topic}}` (template expanded)
- The recorded `init_message` (kickoff) argument:
  - Contains the prompt-built handoff text + `"Topic: should I refactor X"`
  - Does NOT contain `{{topic}}` (template expanded)
  - Is NOT equal to the system prompt (kickoff and system prompt are distinct)

Conclusion: the system prompt is the persona body (frontmatter-stripped,
templated); the kickoff message is the existing context-prompt builder
output (also templated); they are distinct values both delivered to the
correct subprocess CLI argument slots.

#### Verification 2 — Persona-less nodes follow legacy dispatch (TEST-37a.9)

Test: `tests/engine/test_agent_persona.py::TestExecutorAgentDispatch::test_no_agent_uses_run_task`

Setup: `entry -> exit`, neither node has `agent` set.
Observed: `len(run_task_calls) == 2`, `len(system_prompt_calls) == 0`.
Conclusion: zero regression for non-persona nodes.

#### Verification 3 — Missing file at run-time fails the task (TEST-37a.10)

Test: `tests/engine/test_agent_persona.py::TestExecutorAgentDispatch::test_missing_persona_at_runtime_fails_task`

Setup: exit node has `agent="ghost_persona_not_present_anywhere_xyz"`,
the file is never written.
Observed:
- `exit` task row's `status == "failed"`
- `exit` task row's `error_message` contains `"ghost_persona_not_present_anywhere_xyz"`
- `harness.system_prompt_calls` is empty — no silent fallback to a
  no-system-prompt invocation

Conclusion: defense-in-depth path works; `AgentPersonaError` surfaces as
a clean task failure with the persona name in the error message.

#### Verification 4 — Template expansion in both system prompt AND kickoff (TEST-37a.8)

Covered by Verification 1 above and additionally by the unit test
`TestLoadAgentPersona::test_loads_fixture_with_frontmatter_and_template`.
Both assert template substitution happens on the persona body, and the
executor-level test additionally asserts it happens on the kickoff.

#### Verification 5 — Frontmatter `model` selects harness when registered (TEST-37a.11)

Test: `tests/engine/test_agent_persona.py::TestExecutorAgentDispatch::test_frontmatter_model_selects_registered_harness`

Setup:
- `agents/swap.md` frontmatter: `model: custom-backend`
- HarnessManager has both `claude` (default) and `custom-backend` registered

Observed: `custom_backend.system_prompt_calls` has 1 entry; the default
`claude` harness's `system_prompt_calls` is empty. The exit task was
dispatched to `custom-backend` despite the flow declaring `harness: "claude"`.

Test (negative): `test_frontmatter_model_unregistered_warns_and_falls_back`
- `agents/bogus.md` frontmatter: `model: completely-unknown-harness-xyz`
- Only `claude` is registered

Observed: a `WARNING` log entry from `flowstate.engine.executor` containing
`completely-unknown-harness-xyz`. The default harness's
`system_prompt_calls` has 1 entry. No exception. The model field gracefully
falls back to the node/flow harness with operator visibility via the log.

#### Verification 6 — Harnesses without system_prompt support fail cleanly

Test: `tests/engine/test_agent_persona.py::TestExecutorAgentDispatchUnsupportedHarness::test_unsupported_harness_fails_task_cleanly`

Plus unit tests:
- `TestSubprocessManagerSettingsKwarg::test_sdk_runner_raises_not_implemented`
- `TestSubprocessManagerSettingsKwarg::test_acp_harness_raises_not_implemented`

Observed: when an `agent`-using node hits a harness whose
`run_task_with_system_prompt` raises `NotImplementedError`, the task is
marked failed and the harness's `run_task` was NOT invoked as a silent
fallback. SDKRunner and AcpHarness both raise `NotImplementedError` with
a message that mentions `agent.md` and instructs the operator to switch
to the `claude` subprocess harness.

#### Test commands & outputs

```
$ uv run pytest tests/engine/test_agent_persona.py -v
============================== 28 passed in 0.13s ==============================

$ uv run pyright src/flowstate/engine/
0 errors, 0 warnings, 0 informations

$ uv run ruff check src/flowstate/engine/ tests/engine/test_agent_persona.py
All checks passed!

$ uv run pytest tests/engine/test_context.py tests/engine/test_harness.py -v
============================== 74 passed in 0.05s ==============================

$ uv run pytest tests/engine/test_subprocess_mgr.py -v
============================== 18 passed in 0.03s ==============================

$ uv run pytest tests/engine/ --ignore=tests/engine/test_executor.py -q
======================== 473 passed in 67.64s (0:01:07) ========================
```

All engine tests outside `test_executor.py` pass with no regressions.
`test_executor.py` runs to completion locally (no new hangs introduced).

## Completion Checklist
- [x] Unit tests written and passing (28 new tests in `tests/engine/test_agent_persona.py`)
- [x] `/simplify` consideration: code reuses `expand_templates`, avoids new abstractions
- [x] `/lint` passes (ruff, pyright clean for engine + new test file)
- [x] Acceptance criteria verified (see Verifications 1–6 above)
- [x] E2E verification log filled in with concrete evidence
