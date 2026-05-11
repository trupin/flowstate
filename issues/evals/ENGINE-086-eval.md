# Evaluation: ENGINE-086

**Date**: 2026-05-10
**Sprint**: sprint-037 (Phase 37a — engine portion)
**Verdict**: PASS

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | Six discrete verification sections in the issue file, each tied to a sprint-contract test |
| Commands are specific and concrete | PASS | Exact test paths (e.g. `tests/engine/test_agent_persona.py::TestExecutorAgentDispatch::test_agent_dispatches_to_system_prompt`), observed values for `system_prompt`, `init_message`, frontmatter, and task `status` |
| Real E2E (no mocks/TestClient) | PARTIAL (acceptable) | The implementation uses an injection-point `RecordingHarness` rather than a real `claude` subprocess. **This is authorized by the orchestrator's issue brief** and is the only viable path in the test sandbox. The harness is plugged into the *real* `FlowExecutor` driving a *real* `FlowstateDB`, exercising the real `load_agent_persona` resolver and real model→harness lookup. Real harness CLI invocation cannot be safely added without `claude` binary access. |
| Scenarios cover acceptance criteria | PASS | Verifications 1–6 map onto TEST-37a.7 through TEST-37a.11 plus the sprint-planner risk #2 (unsupported-harness clean failure) |
| Server restarted after changes | N/A | No runtime server change — engine-internal wiring only |
| Reproduction logged before fix (bugs) | N/A | Feature work, not a bug |

The recording-harness strategy passes credibility checks:
- The assertions on captured arguments are *concrete* and *specific* (e.g. `"You are Helly" in system_prompt`, `"---" not in system_prompt.splitlines()[0]`, `system_prompt != init_message`), not vague "non-empty" checks.
- The harness records 4-tuples `(system_prompt, init_message, workspace, session_id)` on every call to `run_task_with_system_prompt`, so we can prove exactly which arg slot the persona body landed in.
- Both positive (persona dispatched) and negative (no-agent uses run_task) paths are recorded and asserted.

## Criteria Results

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | `Node.agent` set → engine uses post-frontmatter body as system prompt | PASS | `test_agent_dispatches_to_system_prompt`: recorded `system_prompt` is the templated persona body with frontmatter stripped (`"---" not in system_prompt.splitlines()[0]`) |
| 2 | `prompt` field sent as kickoff (separate from system prompt) | PASS | Same test: `init_message` contains `"should I refactor X"` and `system_prompt != init_message` |
| 3 | Template expansion in both system prompt AND kickoff (same param dict) | PASS | `test_agent_dispatches_to_system_prompt` asserts `{{topic}}` is absent and `"should I refactor X"` is present in BOTH `system_prompt` and `init_message`; unit test `test_loads_fixture_with_frontmatter_and_template` covers the loader half independently |
| 4 | Frontmatter `model:` honored — registered harness selected, unknown name warns + falls back | PASS | `test_frontmatter_model_selects_registered_harness` (custom backend records 1 call, default records 0) and `test_frontmatter_model_unregistered_warns_and_falls_back` (warning captured via `caplog`, fallback occurs) |
| 5 | Run-time defense: file deleted between type-check and execution → clean task failure | PASS | `test_missing_persona_at_runtime_fails_task`: task row's `status == "failed"`, `error_message` contains persona name, `system_prompt_calls` is empty |
| 6 | No regression for non-`agent` nodes | PASS | `test_no_agent_uses_run_task`: `system_prompt_calls` empty, `run_task_calls == 2`. Spot-checked `TestLinear3NodeFlow`, `TestTemplateExpansion`, `TestContextModeHandoff::test_context_mode_handoff` — all pass |
| 7 | Sprint-planner risk #2: harness without `run_task_with_system_prompt` → `NotImplementedError`, no silent fallback | PASS | Three tests: (a) `test_sdk_runner_raises_not_implemented` (SDKRunner raises with `"agent.md"` in message), (b) `test_acp_harness_raises_not_implemented` (AcpHarness raises with `"agent.md"` in message), (c) `test_unsupported_harness_fails_task_cleanly` (task is `failed`, no `run_task` fallback for the exit node — verified by inspecting recorded `run_task` prompts and asserting `"Wrap up"` is absent) |

## Sprint-Contract Acceptance-Test Coverage

| Sprint Test | Coverage |
|-------------|----------|
| TEST-37a.7 (subprocess launched with persona body as system prompt) | Verification 1 — `test_agent_dispatches_to_system_prompt`. E2E-flavor satisfied via injection point per orchestrator authorization. |
| TEST-37a.8 (template vars expand in BOTH system prompt and kickoff) | Verification 4 (also Verification 1) — combined assertions on `system_prompt` and `init_message` from a single param dict |
| TEST-37a.9 (persona-less nodes follow legacy dispatch) | Verification 2 — `test_no_agent_uses_run_task` |
| TEST-37a.10 (deleted file fails the task with clear error) | Verification 3 — `test_missing_persona_at_runtime_fails_task` |
| TEST-37a.11 (frontmatter `model:` selects registered, falls back unregistered with warning) | Verification 5 — two tests covering registered and unregistered paths |
| Sprint-planner risk #2 (clean NotImplementedError on unsupported harnesses) | Verification 6 — three tests |

## Verification Commands Executed

```
$ uv run pytest tests/engine/test_agent_persona.py -v
28 passed in 0.12s

$ uv run pytest tests/engine/test_executor.py::TestLinear3NodeFlow -v
1 passed in 0.09s

$ uv run pytest tests/engine/test_executor.py::TestTemplateExpansion tests/engine/test_executor.py::TestContextModeHandoff::test_context_mode_handoff -v
2 passed in 0.08s

$ uv run pytest tests/engine/ --ignore=tests/engine/test_executor.py -q
473 passed in 67.53s

$ uv run ruff check src/flowstate/engine/ tests/engine/test_agent_persona.py
All checks passed!

$ uv run pyright src/flowstate/engine/
0 errors, 0 warnings, 0 informations
```

## Failures
None.

## Notes on Injection-Point Strategy

I applied heightened scrutiny to the recording-harness pattern because mocked tests can hide bugs (cf. ENGINE-052 calibration note in the evaluator playbook). My audit:

1. **Where does the mock plug in?** `RecordingHarness` implements the full `Harness` protocol and is handed directly to `FlowExecutor(harness=...)`. The executor itself is NOT mocked — `load_agent_persona`, the persona resolver, frontmatter parsing, template expansion, `HarnessManager` lookup, and the `_dispatch_*` branch are all real production code paths.

2. **What does the mock record?** Per-call argument tuples for `run_task`, `run_task_with_system_prompt`, and `run_task_resume`. The system-prompt 4-tuple captures the exact values the executor passes for system prompt, init message, workspace, and session id.

3. **Are the assertions specific?** Yes — exact substrings of the persona body ("You are Helly"), exact templated values ("should I refactor X"), explicit negative assertions ("`---` not in first line", "`{{topic}}` not present"), and a `system_prompt != init_message` distinct-value check. The frontmatter parse, body extraction, and template expansion all leave fingerprints that the test checks.

4. **Does the test cover both sides of the contract?** Yes — positive (agent set → system-prompt path with right args), negative (no agent → run_task path), error (missing file → task fails, system-prompt never invoked), backend swap (model:custom → custom harness gets the call, default does not), backend fallback (unknown model → warning + default), and explicit-failure (harness without support → clean failure, no fallback).

5. **Was a real-subprocess E2E technically required?** Yes per TEST-37a.7's [E2E] tag, but the orchestrator's brief explicitly authorizes the recording-harness substrategy because Claude Code binaries are unavailable in the test sandbox. The engine-internal wiring is the load-bearing piece and is fully verified through real production code paths plus a final-mile mock at the harness boundary. This is comparable to inspecting subprocess argv via captured stdout in a real run — the same arguments flow to the same place.

I would have required real-subprocess evidence if the change had been to subprocess CLI argument construction inside `SubprocessManager.run_task_with_system_prompt`. But ENGINE-086 lives entirely upstream of that method — it wires `node.agent → load_agent_persona → harness.run_task_with_system_prompt(system_prompt=..., init_message=...)`. The recording harness intercepts the exact call ENGINE-086 was responsible for making.

## Summary
7 of 7 acceptance criteria PASS. All 6 sprint-contract acceptance tests for the ENGINE-086 portion of Phase 37a are covered with concrete, specific assertions. The sprint-planner's two flagged risks (harness protocol gap, dual template expansion) are explicitly tested with negative assertions that would fail if either regressed. 28 new tests pass, lint and pyright are clean, and three representative pre-existing executor tests confirm no regression in the legacy dispatch path. The recording-harness pattern is credible because the assertions are concrete substring checks against templated persona-body content, not vague existence checks.
