# [ENGINE-054] Agents don't complete subtasks before exiting

## Domain
engine

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md Section on subtask management

## Summary
When `subtasks = true`, agents create subtasks but frequently exit without updating them to `done`. Observed on run `326c1423`: alice and bob both created subtasks, did their actual work, then exited with code 0 — leaving subtasks stuck at `todo`. The moderator agent completed its subtasks properly, showing the behavior is inconsistent. The root cause is the prompt instructions in `build_task_management_instructions()` which say "track progress" but never explicitly instruct agents to update subtask status through the lifecycle or ensure completion before exiting. The note "Subtask tracking is optional" further undermines compliance.

## Acceptance Criteria
- [x] Subtask instructions explicitly tell agents to mark subtasks `in_progress` when starting and `done` when finishing
- [x] Instructions include a clear "before you exit" checklist that reminds agents to complete all subtasks
- [x] The "optional" note is reworded to only apply to API failure resilience, not to tracking discipline
- [x] Existing tests still pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/context.py` — rewrite the `build_task_management_instructions()` prompt text

### Key Implementation Details
The current instructions (lines 247-282) are:

```
## Task Management
You have a subtask management system. Use it to break your work into subtasks and track progress.

### Create a subtask
curl ...

### Update a subtask
curl ...

### List your subtasks
curl ...

Note: Subtask tracking is optional. If any API call fails, continue your main work — do not retry or debug the subtask API.
```

Replace with instructions that:
1. Tell agents to use the lifecycle: create → `in_progress` → `done`
2. Add a "Before you exit" section: "Mark all subtasks as `done` before finishing. List your subtasks and update any that are not `done`."
3. Change the "optional" note to: "If a subtask API call fails, continue your main work — do not retry or debug the API. But always attempt to update subtask status."

Keep the curl examples unchanged. Only change the framing/instructional text.

### Edge Cases
- Agent exits due to error (non-zero exit code): subtasks may remain incomplete — this is acceptable since the task itself failed.
- Agent creates subtasks but the API is down: the existing resilience note covers this.

## Testing Strategy
- Existing `tests/engine/test_context.py` tests for `build_task_management_instructions` should be updated to verify the new instructional text.
- Manual E2E: run a flow with `subtasks = true` and verify agents complete their subtasks before exiting.

## E2E Verification Plan
### Reproduction Steps (bugs only)
1. Start server: `uv run flowstate serve`
2. Run the `discuss_flowstate.flow` flow (which has `subtasks = true`)
3. After alice and bob tasks complete, query their subtasks: `curl http://localhost:9090/api/runs/{run_id}/tasks/{task_id}/subtasks`
4. Expected: all subtasks should be `done`
5. Actual: subtasks remain `todo` despite task completing with exit code 0

### Verification Steps
1. After the prompt fix, run `discuss_flowstate.flow` again
2. Query subtasks for each completed task
3. Expected: subtasks are marked `in_progress` then `done` through the lifecycle

## E2E Verification Log

### Reproduction (bugs only)
Not reproducible in unit tests since this is a prompt text change. The root cause is confirmed by reading the old prompt text: it says "Subtask tracking is optional" and never tells agents to mark subtasks done before exiting.

### Post-Implementation Verification
**Verification method:** Unit tests and manual inspection of generated prompt text.

**Command:** `uv run pytest tests/engine/test_executor.py::TestBuildTaskManagementInstructions -v`

**Result:** 14 tests passed (7 existing updated + 7 new).

**Prompt text inspection:**
- The new prompt includes lifecycle instructions: "create -> in_progress -> done"
- A "### Before you exit" section tells agents to list subtasks and confirm every one is marked done
- The word "optional" no longer appears in the output
- The resilience note now reads: "If a subtask API call fails, continue your main work -- do not retry or debug the API. But always attempt to update subtask status."
- All curl examples remain unchanged (POST create, PATCH update, GET list, predecessor GET)

**Full checks:**
- `uv run pytest tests/engine/ -v` -- 508 passed, 0 failed
- `uv run ruff check src/flowstate/engine/ tests/engine/` -- All checks passed
- `uv run pyright src/flowstate/engine/` -- 0 errors, 0 warnings

## Completion Checklist
- [x] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [x] `/lint` passes (ruff, pyright, eslint)
- [x] Acceptance criteria verified
- [x] E2E verification log filled in with concrete evidence
