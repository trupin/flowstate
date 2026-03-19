# [ENGINE-016] Orchestrator as Judge

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
- specs.md Section 7 — "Judge Protocol"
- specs.md Section 9 — "Claude Code Integration"

## Summary
Add an alternative judge evaluation path that uses the orchestrator session instead of spawning a separate judge subprocess. The orchestrator already runs on Sonnet and has accumulated context about the flow, making it both cheaper and faster for judge evaluations. The engine writes REQUEST.md, resumes the orchestrator with a judge instruction, and reads DECISION.json after the orchestrator writes it.

## Acceptance Criteria
- [ ] `JudgeProtocol` accepts optional `OrchestratorManager` in constructor
- [ ] `evaluate_via_orchestrator(context, orchestrator_session)` method added
- [ ] Method writes REQUEST.md with judge context
- [ ] Method resumes orchestrator with judge instruction
- [ ] Method reads DECISION.json after orchestrator completes
- [ ] Existing `evaluate()` method updated: tries orchestrator first, falls back to subprocess
- [ ] Retry logic preserved: on orchestrator judge failure, retry once via orchestrator, then fall back to direct subprocess
- [ ] All existing judge tests continue to pass
- [ ] All new tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/judge.py` — Add `evaluate_via_orchestrator()`, modify `evaluate()`
- `src/flowstate/engine/orchestrator.py` — Add `build_judge_instruction()` helper
- `tests/engine/test_judge_orchestrator.py` — Tests for orchestrator judge path

### Key Implementation Details

#### evaluate_via_orchestrator

```python
async def evaluate_via_orchestrator(
    self, context: JudgeContext, session: OrchestratorSession, run_data_dir: str
) -> JudgeDecision:
    """Evaluate conditional edges via the orchestrator session."""
    judge_dir = create_judge_dir(run_data_dir, context.node_name, ...)
    write_judge_request(judge_dir, context)

    instruction = build_judge_instruction(context, judge_dir)
    # Resume orchestrator session
    stream = self._subprocess_mgr.run_task_resume(
        instruction, context.task_cwd, session.session_id, ...
    )
    async for event in stream:
        pass  # Wait for completion

    return read_judge_decision(judge_dir)
```

#### Judge Instruction (sent to orchestrator on resume)

```
Evaluate the transition from task "{node_name}".

Read the evaluation request from: {request_path}
Write your decision to: {decision_path}

Your decision must be a JSON object: {"decision": "<target>", "reasoning": "...", "confidence": 0.0-1.0}
Available targets: {targets}
Use "__none__" if no condition clearly matches.
```

#### Modified evaluate()

```python
async def evaluate(self, context: JudgeContext) -> JudgeDecision:
    # Try orchestrator path first
    if self._orchestrator_mgr is not None:
        session = await self._orchestrator_mgr.get_or_create(...)
        try:
            return await self.evaluate_via_orchestrator(context, session, ...)
        except Exception:
            pass  # Fall through to direct subprocess

    # Existing direct subprocess path (unchanged)
    prompt = build_judge_prompt(context)
    ...
```

### Edge Cases
- Orchestrator writes malformed DECISION.json — parse error triggers retry
- Orchestrator doesn't write DECISION.json — FileNotFoundError triggers retry
- Orchestrator session expired — fall back to direct subprocess
- Confidence < 0.5 — handled the same as current (pause flow)

## Testing Strategy
1. **test_evaluate_via_orchestrator** — Mock orchestrator, verify REQUEST.md written and DECISION.json read
2. **test_evaluate_orchestrator_fallback** — Orchestrator fails, verify fallback to direct subprocess
3. **test_evaluate_orchestrator_retry** — First attempt fails, retry succeeds
4. **test_evaluate_without_orchestrator** — No orchestrator, verify existing behavior unchanged
