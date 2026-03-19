# [ENGINE-015] Orchestrator as Task Executor

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-012, ENGINE-014
- Blocks: —

## Spec References
- specs.md Section 6 — "Execution Model"
- specs.md Section 9 — "Claude Code Integration"

## Summary
Modify `_execute_single_task()` in `executor.py` to route task execution through the orchestrator agent instead of directly spawning Claude Code subprocesses. The orchestrator session is resumed with a short instruction telling it to read INPUT.md, spawn a subagent, and ensure SUMMARY.md is written. Falls back to direct subprocess on orchestrator init failure.

## Acceptance Criteria
- [ ] `_execute_single_task()` checks for orchestrator session availability
- [ ] When orchestrator available: writes INPUT.md, resumes orchestrator with task instruction
- [ ] Orchestrator resume instruction references INPUT.md path and expected SUMMARY.md path
- [ ] Stream events from orchestrator process are forwarded to UI
- [ ] For forks: orchestrator receives all fork branches in one resume (parallel Agent calls)
- [ ] Fallback: if orchestrator not available or init fails, fall back to direct subprocess
- [ ] FlowExecutor accepts optional OrchestratorManager in constructor
- [ ] Existing tests continue to pass (backward compatible)
- [ ] All new tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — Modify `_execute_single_task()`, add orchestrator integration
- `src/flowstate/engine/orchestrator.py` — Add helper methods for task/judge instructions
- `tests/engine/test_executor_orchestrator.py` — Tests for orchestrator execution path

### Key Implementation Details

#### Modified _execute_single_task

```python
async def _execute_single_task(self, ...):
    # If orchestrator manager is available, route through orchestrator
    if self._orchestrator_mgr is not None:
        try:
            session = await self._orchestrator_mgr.get_or_create(
                harness="claude", cwd=task_exec.cwd, flow=flow,
                run_id=flow_run_id, run_data_dir=data_dir
            )
            # Write INPUT.md
            write_task_input(task_exec.task_dir, task_exec.prompt_text)
            # Resume orchestrator with task instruction
            instruction = build_task_instruction(task_exec, session)
            stream = self._subprocess_mgr.run_task_resume(
                instruction, task_exec.cwd, session.session_id, ...
            )
            # Process stream events as before...
        except Exception:
            # Fallback to direct subprocess
            ...
    else:
        # Existing direct subprocess path (unchanged)
        ...
```

#### Task Instruction (sent to orchestrator on resume)

Short instruction that tells the orchestrator what to do:
```
Execute task "{node_name}" (generation {gen}).

Read the full task context from: {input_path}
Spawn a subagent to execute the task. The subagent should:
- Work in directory: {cwd}
- Write SUMMARY.md to: {task_dir}/SUMMARY.md

Use the Agent tool with model: "opus" to spawn the subagent.
```

### Edge Cases
- Orchestrator session expired/crashed — detect via process exit, fall back to direct
- INPUT.md write fails (disk full) — propagate error, task fails
- Orchestrator doesn't write SUMMARY.md — same handling as current (warning, no crash)
- Backward compatibility — when no OrchestratorManager provided, behavior identical to current

## Testing Strategy
1. **test_execute_with_orchestrator** — Mock orchestrator, verify INPUT.md written and session resumed
2. **test_execute_fallback** — Orchestrator init fails, verify fallback to direct subprocess
3. **test_execute_without_orchestrator** — No orchestrator manager, verify existing behavior
4. **test_fork_via_orchestrator** — Fork targets sent to orchestrator in single resume
