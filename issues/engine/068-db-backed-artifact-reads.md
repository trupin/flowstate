# [ENGINE-068] Move all artifact I/O to DB; eliminate ~/.flowstate/runs/ directory tree

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-067
- Blocks: E2E-015

## Spec References
- specs.md Section 9.6 — "API-Based Artifact Protocol"
- specs.md Section 6.5 — "Conditional Edge Execution"
- specs.md Section 8 — "Context Passing"

## Summary
Eliminate all filesystem-based artifact I/O. Both agent-submitted artifacts (decision, summary, output) and engine-written artifacts (input prompt, judge request, judge decision) move to the database. This removes the entire `~/.flowstate/runs/<run-id>/` directory tree — no more `create_task_dir()`, `create_judge_dir()`, or `task_dir` column. The sandbox `download_file()` mechanism and connect-wrapper-based downloads are also removed.

## Acceptance Criteria

**Agent artifact reads (from DB instead of filesystem):**
- [ ] `_acquire_routing_decision()` reads decision from DB via `db.get_artifact(task_id, "decision")` for self-report mode
- [ ] Context assembly reads summary from DB via `db.get_artifact(task_id, "summary")`
- [ ] Cross-flow filing reads output from DB via `db.get_artifact(task_id, "output")`
- [ ] Judge reads summary from DB when building judge prompt

**Engine artifact writes (to DB instead of filesystem):**
- [ ] `write_task_input()` replaced: engine saves assembled prompt as `db.save_artifact(task_id, "input", prompt, "text/markdown")`
- [ ] `write_judge_request()` replaced: engine saves judge prompt as `db.save_artifact(task_id, "judge_request", prompt, "text/markdown")`
- [ ] `write_judge_decision()` replaced: engine saves judge decision as `db.save_artifact(task_id, "judge_decision", json, "application/json")`

**Filesystem removal:**
- [ ] `create_task_dir()` removed from `context.py`
- [ ] `create_judge_dir()` removed from `context.py`
- [ ] `task_dir` column no longer written to or read from in `task_executions` (keep column for backwards compat, write empty string)
- [ ] `read_summary()`, `read_output_json()` removed from `context.py`
- [ ] `read_judge_decision()`, `write_judge_request()`, `write_judge_decision()` removed from `judge.py`
- [ ] `write_task_input()` removed from `context.py`
- [ ] `SandboxManager.download_file()` method removed from `sandbox.py`
- [ ] DECISION.json download block removed from executor
- [ ] `build_routing_instructions()` no longer takes `task_dir` parameter
- [ ] `_build_directory_sections()` no longer references task coordination directory
- [ ] `~/.flowstate/runs/` directory is no longer created or used
- [ ] `data_dir` column in `flow_runs`: write empty string (NOT NULL constraint, keep for backwards compat)
- [ ] `task_dir` column in `task_executions`: write empty string (keep for backwards compat)
- [ ] Run results endpoint (`_compute_run_results()` in routes.py) reads summaries from DB instead of filesystem
- [ ] Artifact read includes brief poll (up to 5s, 0.5s intervals) before declaring missing — handles race where agent POSTs artifact just before process exit
- [ ] `MockSubprocessManager._write_summary()` in `tests/e2e/mock_subprocess.py` updated to save artifacts via DB instead of writing to filesystem
- [ ] All 8+ E2E test files that use MockSubprocessManager continue to pass
- [ ] All existing tests updated
- [ ] Flows with conditional edges, context handoff, and cross-flow filing work correctly

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — change all artifact reads/writes to DB-backed; remove task_dir/judge_dir creation; remove sandbox download block
- `src/flowstate/engine/context.py` — remove `create_task_dir()`, `create_judge_dir()`, `read_summary()`, `read_output_json()`, `write_task_input()`; update `_build_directory_sections()`
- `src/flowstate/engine/judge.py` — remove `read_judge_decision()`, `write_judge_request()`, `write_judge_decision()`
- `src/flowstate/engine/sandbox.py` — remove `download_file()` method
- `tests/engine/test_executor.py` — update tests
- `tests/engine/test_context.py` — update tests
- `tests/engine/test_judge.py` — update tests
- `tests/e2e/mock_subprocess.py` — update `_write_summary()` to save artifact via DB instead of writing to filesystem (used by 8+ E2E test files)
- `tests/e2e/test_unit_test_gen_flow.py` — update RoutingMockSubprocessManager to save artifacts via DB
- `src/flowstate/server/routes.py` — update `_compute_run_results()` to read summaries from DB

### Key Implementation Details

**Self-report routing (executor.py `_acquire_routing_decision`):**

Replace:
```python
return read_judge_decision(task_exec.task_dir)
```
With:
```python
artifact = self._db.get_artifact(task_exec.id, "decision")
if artifact is None:
    raise FileNotFoundError("No decision artifact submitted by agent")
import json
data = json.loads(artifact.content)
return JudgeDecision(
    target=data["decision"],
    reasoning=data["reasoning"],
    confidence=float(data["confidence"]),
)
```

**Judge summary reading (executor.py):**

Replace all calls to `read_summary(task_exec.task_dir)` with:
```python
artifact = self._db.get_artifact(task_exec.id, "summary")
summary = artifact.content if artifact else None
```

**Context assembly — handoff mode (executor.py):**

When building handoff prompts, replace:
```python
predecessor_summary = read_summary(predecessor_task_dir)
```
With:
```python
artifact = self._db.get_artifact(predecessor_task_id, "summary")
predecessor_summary = artifact.content if artifact else None
```

**Context assembly — join mode (executor.py):**

When aggregating fork member summaries, replace filesystem reads with DB reads:
```python
member_summaries = {}
for member_task_id, member_name in fork_members:
    artifact = self._db.get_artifact(member_task_id, "summary")
    member_summaries[member_name] = artifact.content if artifact else None
```

**Cross-flow filing (executor.py):**

Replace:
```python
output = read_output_json(task_exec.task_dir)
```
With:
```python
artifact = self._db.get_artifact(task_exec.id, "output")
if artifact:
    raw = json.loads(artifact.content)
    output = {k: v for k, v in raw.items() if isinstance(v, str | int | float | bool)}
else:
    output = None
```

**Engine writes — INPUT.md replacement (executor.py):**

In `_create_task_execution()`, replace:
```python
task_dir = create_task_dir(data_dir, node.name, generation)
write_task_input(task_dir, prompt)
```
With:
```python
self._db.save_artifact(task_execution_id, "input", prompt, "text/markdown")
```

No `task_dir` needed. The `task_execution_id` is the key.

**Engine writes — judge artifacts (executor.py + judge.py):**

Replace `create_judge_dir()` + `write_judge_request()` with:
```python
self._db.save_artifact(task_exec.id, "judge_request", judge_prompt, "text/markdown")
```

Replace `write_judge_decision()` with:
```python
self._db.save_artifact(task_exec.id, "judge_decision", json.dumps(decision_data), "application/json")
```

Note: judge artifacts are stored on the source task's execution ID (the task being evaluated), not a separate judge entity.

**Remove task_dir from executor (executor.py):**

- Remove all `create_task_dir()` calls (lines ~809, 1224, 1594, 2668)
- Remove all `create_judge_dir()` calls
- Set `task_dir=""` in task_execution records (column kept for backwards compat)
- Remove `data_dir` tracking from executor (no longer needed for filesystem paths)
- Remove `task_exec.task_dir` references in routing/context code

**Remove DECISION.json download block (executor.py lines 2555-2573):**

Delete the entire `if use_sandbox and exit_code == 0:` block. No longer needed.

**Remove `SandboxManager.download_file()` (sandbox.py):**

Delete the `download_file()` method entirely. The `SandboxManager` only needs `wrap_command()`.

**Update `build_routing_instructions()` call sites:**

Remove the `task_dir` parameter from all calls. The function now references `$FLOWSTATE_TASK_ID` env var for the API URL (done in ENGINE-067).

**Remove `_build_directory_sections()` task coordination section (context.py):**

The "Task coordination directory" section is removed entirely. The "Working directory" section remains. No more "Write coordination files to {task_dir}/" instruction.

**Update mock subprocess in E2E tests:**

The `RoutingMockSubprocessManager` in `test_unit_test_gen_flow.py` currently writes DECISION.json to the filesystem. Update it to save artifacts via the DB:
```python
db.save_artifact(task_id, "decision", json.dumps(decision_data), "application/json")
db.save_artifact(task_id, "summary", summary_text, "text/markdown")
```

### Edge Cases
- Agent doesn't submit artifact: `get_artifact()` returns None → flow pauses with clear error message
- Agent submits malformed JSON decision: `json.loads()` raises → flow pauses with parse error
- Agent submits decision with missing fields: validation catches → flow pauses
- Timing: agent may submit artifact after task completion event but before engine reads it — add a brief poll (up to 5s) before declaring missing
- Legacy flows: old runs with file-based artifacts won't have DB records. This is acceptable — only new runs use the API.

## Testing Strategy
- Update `test_executor.py`: mock `db.get_artifact()` to return test data
- Update `test_judge.py`: remove file-based `read_judge_decision()` tests, add DB-backed tests
- Update `test_context.py`: remove `read_summary()` and `read_output_json()` tests
- Update `test_unit_test_gen_flow.py`: mock subprocess writes to API instead of filesystem
- New test: verify flow pauses with clear error when decision artifact is missing

## E2E Verification Plan

### Verification Steps
1. Start server: `uv run flowstate server`
2. Run a flow with conditional edges (self-report mode)
3. Verify agent submits decision via API (check server logs)
4. Verify flow routes correctly based on API-submitted decision
5. Run a flow with context=handoff
6. Verify successor task receives predecessor's summary
7. Run sandbox E2E test suite: `uv run pytest tests/e2e/test_sandbox.py -v`

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
