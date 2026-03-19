# [ENGINE-007] Executor — Conditional + Cycles

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-004, ENGINE-005
- Blocks: none

## Spec References
- specs.md Section 6.5 — "Conditional Branching"
- specs.md Section 6.6 — "Cycle Re-entry"
- specs.md Section 7 — "Judge Protocol" (full section)
- agents/03-engine.md — "Conditional Branching", "Cycle Re-entry"

## Summary
Extend the `FlowExecutor` to handle conditional edges and cycle re-entry. When a completed task has conditional outgoing edges, the executor invokes the judge protocol to evaluate which transition to take. The judge reads the task's SUMMARY.md and workspace, then returns a decision (target node + reasoning + confidence). If the judge chooses `"__none__"` or has low confidence (< 0.5), the flow pauses for human review. For cycles (conditional edge targeting an already-executed node), the executor increments the generation counter, creates a new task directory, and applies the edge's context mode to determine how context flows into the re-entered task.

## Acceptance Criteria
- [ ] Conditional edge handling added to the executor's edge evaluation logic
- [ ] When a node with conditional outgoing edges completes: the judge is invoked via `JudgeProtocol.evaluate()`
- [ ] `judge.started` event is emitted before judge invocation
- [ ] `judge.decided` event is emitted after judge returns
- [ ] The judge's decision routes to the correct target node
- [ ] `edge.transition` event is emitted with judge reasoning
- [ ] On `"__none__"` decision: flow pauses with reason "Judge could not match any condition"
- [ ] On low confidence (< 0.5): flow pauses with tentative decision shown in the event
- [ ] On judge failure (JudgePauseError): flow pauses with failure reason
- [ ] Cycle re-entry: generation is incremented for the re-entered node
- [ ] Cycle re-entry: new task_execution record is created with the incremented generation
- [ ] Cycle re-entry: new task directory is created (`<name>-<new_gen>/`)
- [ ] Cycle re-entry with handoff mode: fresh session, predecessor SUMMARY.md + judge feedback injected
- [ ] Cycle re-entry with session mode: resumes the *source* task's session (the reviewer, not the previous iteration)
- [ ] Cycle re-entry with none mode: fresh session, only the task's own prompt
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — extend with conditional + cycle handling
- `tests/engine/test_executor.py` — add conditional and cycle tests

### Key Implementation Details

#### Conditional Edge Handling

Add a branch to the edge evaluation section of the main loop:

```python
# In the main loop, edge evaluation:
outgoing = _get_outgoing_edges(flow, task_exec.node_name)

if _is_conditional(outgoing):
    # Build judge context
    summary = read_summary(task_exec.task_dir)
    judge_context = JudgeContext(
        node_name=task_exec.node_name,
        task_prompt=task_exec.prompt_text,
        exit_code=task_exec.exit_code or 0,
        summary=summary,
        task_cwd=task_exec.cwd,
        run_id=flow_run_id,
        outgoing_edges=[
            (e.condition, e.target)
            for e in outgoing
            if e.edge_type == EdgeType.CONDITIONAL and e.condition and e.target
        ],
    )

    self._emit(FlowEvent(
        type=EventType.JUDGE_STARTED,
        flow_run_id=flow_run_id,
        timestamp=_now_iso(),
        payload={
            "from_node": task_exec.node_name,
            "conditions": [c for c, _ in judge_context.outgoing_edges],
        },
    ))

    try:
        decision = await self._judge.evaluate(judge_context)
    except JudgePauseError as e:
        self._pause_flow(flow_run_id, f"Judge failed: {e.reason}")
        continue

    self._emit(FlowEvent(
        type=EventType.JUDGE_DECIDED,
        flow_run_id=flow_run_id,
        timestamp=_now_iso(),
        payload={
            "from_node": task_exec.node_name,
            "to_node": decision.target,
            "reasoning": decision.reasoning,
            "confidence": decision.confidence,
        },
    ))

    # Handle special cases
    if decision.is_none:
        self._pause_flow(
            flow_run_id,
            "Judge could not match any condition",
        )
        continue

    if decision.is_low_confidence:
        self._pause_flow(
            flow_run_id,
            f"Judge has low confidence ({decision.confidence:.2f}) "
            f"for transition to '{decision.target}': {decision.reasoning}",
        )
        continue

    # Find the matching edge
    chosen_edge = next(
        e for e in outgoing
        if e.edge_type == EdgeType.CONDITIONAL and e.target == decision.target
    )

    # Determine if this is a cycle re-entry
    is_cycle = _has_been_executed(flow_run_id, decision.target, self._db)
    target_gen = _get_next_generation(flow_run_id, decision.target, self._db) if is_cycle else 1

    ctx_mode = get_context_mode(chosen_edge, flow)
    target_node = flow.nodes[decision.target]

    # Create task execution for the target
    next_task_id = self._create_task_execution_conditional(
        flow_run_id=flow_run_id,
        target_node=target_node,
        generation=target_gen,
        flow=flow,
        expanded_prompt=expanded_prompts[decision.target],
        data_dir=data_dir,
        context_mode=ctx_mode,
        source_task=task_exec,
        judge_decision=decision,
        is_cycle=is_cycle,
    )
    pending.add(next_task_id)

    # Record edge transition
    self._db.create_edge_transition(
        id=str(uuid.uuid4()),
        flow_run_id=flow_run_id,
        from_task_id=task_exec.id,
        to_task_id=next_task_id,
        edge_type="conditional",
        condition_text=chosen_edge.condition,
        judge_decision=decision.target,
        judge_reasoning=decision.reasoning,
        judge_confidence=decision.confidence,
    )

    self._emit(FlowEvent(
        type=EventType.EDGE_TRANSITION,
        flow_run_id=flow_run_id,
        timestamp=_now_iso(),
        payload={
            "from_node": task_exec.node_name,
            "to_node": decision.target,
            "edge_type": "conditional",
            "condition": chosen_edge.condition,
            "judge_reasoning": decision.reasoning,
        },
    ))
```

#### Cycle Re-entry Handling

```python
def _create_task_execution_conditional(
    self, flow_run_id: str, target_node: Node, generation: int,
    flow: Flow, expanded_prompt: str, data_dir: str,
    context_mode: ContextMode, source_task, judge_decision: JudgeDecision,
    is_cycle: bool,
) -> str:
    """Create task execution for a conditional transition, handling cycles."""
    task_id = str(uuid.uuid4())
    task_dir = create_task_dir(data_dir, target_node.name, generation)
    cwd = resolve_cwd(target_node, flow)

    if is_cycle and context_mode == ContextMode.HANDOFF:
        # For cycle re-entry with handoff: include source task's summary
        # AND the judge's reasoning as feedback
        source_summary = read_summary(source_task.task_dir)
        cycle_context = (
            f"{source_summary or '(No summary available)'}\n\n"
            f"## Judge Feedback\n"
            f"The reviewing judge decided: {judge_decision.reasoning}\n"
            f"You are re-entering this task (generation {generation}) to address the feedback."
        )
        prompt = build_prompt_handoff(target_node, task_dir, cwd, cycle_context)

    elif is_cycle and context_mode == ContextMode.SESSION:
        # Resume the SOURCE task's session (the reviewer), not the
        # target's previous session. The source task's session carries
        # the review context.
        prompt = build_prompt_session(target_node, task_dir)
        # The caller must pass source_task.claude_session_id for --resume

    elif context_mode == ContextMode.HANDOFF:
        # Normal (non-cycle) conditional transition
        source_summary = read_summary(source_task.task_dir)
        prompt = build_prompt_handoff(target_node, task_dir, cwd, source_summary)

    elif context_mode == ContextMode.SESSION:
        prompt = build_prompt_session(target_node, task_dir)

    else:  # none
        prompt = build_prompt_none(target_node, task_dir, cwd)

    self._db.create_task_execution(
        id=task_id,
        flow_run_id=flow_run_id,
        node_name=target_node.name,
        node_type=target_node.node_type.value,
        status="pending",
        generation=generation,
        context_mode=context_mode.value,
        cwd=cwd,
        task_dir=task_dir,
        prompt_text=prompt,
        claude_session_id=source_task.claude_session_id if context_mode == ContextMode.SESSION else None,
    )
    return task_id


def _has_been_executed(flow_run_id: str, node_name: str, db: FlowstateDB) -> bool:
    """Check if a node has any completed executions in this run."""
    executions = db.get_task_executions_for_node(flow_run_id, node_name)
    return any(e.status in ("completed", "failed", "skipped") for e in executions)


def _get_next_generation(flow_run_id: str, node_name: str, db: FlowstateDB) -> int:
    """Get the next generation number for a node in a run."""
    executions = db.get_task_executions_for_node(flow_run_id, node_name)
    if not executions:
        return 1
    return max(e.generation for e in executions) + 1
```

#### Detecting Conditional Edges

```python
def _is_conditional(edges: list[Edge]) -> bool:
    """Check if any outgoing edge is conditional."""
    return any(e.edge_type == EdgeType.CONDITIONAL for e in edges)
```

### Edge Cases
- **All conditional edges rejected (`__none__`)**: Flow pauses. The user can override the judge's decision via the web UI.
- **Low confidence on a valid target**: Flow pauses with the tentative decision visible. User can accept or override.
- **Judge subprocess crash (JudgePauseError)**: After one retry (handled by JudgeProtocol), the flow pauses. The user can retry the judge evaluation via the UI.
- **Cycle with session mode**: The resumed session is the *source* task's (the reviewer's) session, not the target's previous session. This means the agent that reviewed the work continues into the implementation, carrying its review context. The `claude_session_id` stored in the new task execution is the source task's session ID.
- **Multiple cycles**: Each re-entry increments generation. Task directories are `<name>-1/`, `<name>-2/`, `<name>-3/`, etc.
- **Generation overflow**: No practical limit on generations. The integer counter grows monotonically.
- **Conditional edge with only one option**: Still invokes the judge (plus `__none__`). The judge might still choose `__none__` if the condition isn't met.
- **Exit node reachable via conditional edge**: Valid topology. When the exit node completes, the flow completes regardless of whether it was reached via unconditional or conditional edge.

## Testing Strategy

Add to `tests/engine/test_executor.py`:

1. **test_conditional_branch_happy_path** — Flow: `entry -> review`, `review -> done when "approved"`, `review -> implement when "needs work"`. Mock judge to return `decision="done"`. Verify the `done` task is enqueued, judge events emitted.

2. **test_conditional_branch_alternative** — Same flow, mock judge returns `decision="implement"`. Verify `implement` is enqueued.

3. **test_conditional_none_pauses** — Mock judge returns `decision="__none__"`. Verify flow pauses with reason "Judge could not match any condition".

4. **test_conditional_low_confidence_pauses** — Mock judge returns `confidence=0.3`. Verify flow pauses with tentative decision shown.

5. **test_conditional_judge_failure_pauses** — Mock JudgeProtocol.evaluate to raise JudgePauseError. Verify flow pauses.

6. **test_cycle_generation_increment** — Flow: `entry -> implement -> review`, `review -> done when "approved"`, `review -> implement when "needs work"`. Mock judge to return "needs work" on first call, "approved" on second. Verify:
   - `implement` runs at generation 1, then generation 2
   - `review` runs at generation 1, then generation 2 (second review after the second implement)
   - Two task directories exist: `implement-1/` and `implement-2/`

7. **test_cycle_three_iterations** — Mock judge returns "needs work" twice, then "approved". Verify generation reaches 3 for the cycled node.

8. **test_cycle_handoff_context** — Cycle with handoff mode. Verify the re-entered task's prompt includes the source task's summary AND the judge's reasoning as feedback.

9. **test_cycle_session_context** — Cycle with session mode. Verify the re-entered task's `claude_session_id` is the source task's (reviewer's) session ID, not the previous iteration's.

10. **test_cycle_none_context** — Cycle with none mode. Verify the re-entered task's prompt contains only the node's own prompt.

11. **test_edge_transition_recorded** — After a conditional transition, verify an `edge_transitions` record exists in DB with judge decision/reasoning/confidence.

12. **test_judge_events_emitted** — Verify `judge.started` and `judge.decided` events in the correct order with correct payloads.

Mock the `JudgeProtocol` entirely using `unittest.mock.AsyncMock`. Configure it to return predetermined `JudgeDecision` objects for each call.
