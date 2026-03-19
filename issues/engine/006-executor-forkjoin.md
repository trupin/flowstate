# [ENGINE-006] Executor — Fork-Join

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-005, STATE-005
- Blocks: none

## Spec References
- specs.md Section 6.3 — "Execution Algorithm" (fork/join handling in the pseudocode)
- specs.md Section 6.4 — "Fork-Join Execution"
- specs.md Section 6.7 — "Concurrency Controls"
- specs.md Section 9.1 — "Prompt construction for join nodes"
- agents/03-engine.md — "Fork-Join Coordination"

## Summary
Extend the `FlowExecutor` to handle fork-join topology. When a fork edge is encountered after a task completes, the executor creates pending tasks for ALL fork targets simultaneously and links them via a `fork_group` record in the database. Forked tasks run in parallel (up to the semaphore limit). When a forked task completes, the executor checks whether all members of its fork group have completed. When all are done, the join target task is enqueued with an aggregated context from all fork members' SUMMARY.md files. Generation tracking ensures all tasks in a fork group share the same generation, and the join target gets the next generation.

## Acceptance Criteria
- [ ] Fork edge handling added to the executor's edge evaluation logic
- [ ] When a fork edge is encountered: all target tasks are created as `pending` simultaneously
- [ ] A `fork_group` record is created in the DB linking all fork member tasks
- [ ] `fork_group_members` records are created for each member
- [ ] Fork group has status `active` when created
- [ ] `fork.started` event is emitted with fork_group_id, source_node, and target names
- [ ] Forked tasks run in parallel, bounded by the concurrency semaphore
- [ ] When a forked task completes: the executor checks if all members of the fork group are `completed`
- [ ] When all fork members complete: fork group status is updated to `joined`
- [ ] Join target task is enqueued with context aggregated from all fork members
- [ ] `fork.joined` event is emitted with fork_group_id and join_node name
- [ ] Join prompt uses `build_prompt_join` with all member SUMMARY.md contents
- [ ] All fork members share the same generation
- [ ] Join target gets generation = fork member generation + 1
- [ ] `edge.transition` events are emitted for both fork and join edges
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/executor.py` — extend with fork-join handling
- `tests/engine/test_executor.py` — add fork-join tests

### Key Implementation Details

#### Fork Edge Handling (in main loop, edge evaluation section)

Add a branch to the edge evaluation after task completion:

```python
# In the main loop, after task completion, evaluating outgoing edges:
outgoing = _get_outgoing_edges(flow, task_exec.node_name)

if _is_fork(outgoing):
    # Find the fork edge
    fork_edge = next(e for e in outgoing if e.edge_type == EdgeType.FORK)
    assert fork_edge.fork_targets is not None

    # Find the corresponding join edge to know the join node
    join_node_name = _find_join_node(flow, fork_edge.fork_targets)

    # Create fork group
    fork_group_id = str(uuid.uuid4())
    gen = task_exec.generation  # all fork members share this generation
    self._db.create_fork_group(
        id=fork_group_id,
        flow_run_id=flow_run_id,
        source_task_id=task_exec.id,
        join_node_name=join_node_name,
        generation=gen,
        status="active",
    )

    # Create task executions for all fork targets
    member_task_ids: list[str] = []
    for target_name in fork_edge.fork_targets:
        target_node = flow.nodes[target_name]
        ctx_mode = get_context_mode(fork_edge, flow)
        # Fork always uses handoff — the type checker enforces this
        task_id = self._create_task_execution(
            flow_run_id, target_node, generation=gen,
            flow=flow, expanded_prompt=expanded_prompts[target_name],
            data_dir=data_dir, context_mode=ctx_mode,
            predecessor_task_id=completed_id,
        )
        member_task_ids.append(task_id)
        self._db.add_fork_group_member(fork_group_id, task_id)
        pending.add(task_id)

    self._emit(FlowEvent(
        type=EventType.FORK_STARTED,
        flow_run_id=flow_run_id,
        timestamp=_now_iso(),
        payload={
            "fork_group_id": fork_group_id,
            "source_node": task_exec.node_name,
            "targets": list(fork_edge.fork_targets),
        },
    ))
```

#### Join Check (after each task completion)

```python
# After processing a completed task's outgoing edges, check for fork-join:
fork_group = self._db.get_fork_group_for_member(completed_id)
if fork_group and fork_group.status == "active":
    members = self._db.get_fork_group_members(fork_group.id)
    all_completed = all(
        self._db.get_task_execution(m.task_execution_id).status == "completed"
        for m in members
    )
    if all_completed:
        # Mark fork group as joined
        self._db.update_fork_group_status(fork_group.id, "joined")

        # Collect summaries from all members
        member_summaries: list[tuple[str, str | None]] = []
        for m in members:
            m_task = self._db.get_task_execution(m.task_execution_id)
            summary = read_summary(m_task.task_dir)
            member_summaries.append((m_task.node_name, summary))

        # Enqueue join target
        join_node = flow.nodes[fork_group.join_node_name]
        join_gen = fork_group.generation + 1
        task_dir = create_task_dir(data_dir, join_node.name, join_gen)
        cwd = resolve_cwd(join_node, flow)
        prompt = build_prompt_join(join_node, task_dir, cwd, member_summaries)

        join_task_id = self._db.create_task_execution(
            id=str(uuid.uuid4()),
            flow_run_id=flow_run_id,
            node_name=join_node.name,
            node_type=join_node.node_type.value,
            status="pending",
            generation=join_gen,
            context_mode=ContextMode.HANDOFF.value,  # joins are always handoff
            cwd=cwd,
            task_dir=task_dir,
            prompt_text=prompt,
        )
        pending.add(join_task_id)

        self._emit(FlowEvent(
            type=EventType.FORK_JOINED,
            flow_run_id=flow_run_id,
            timestamp=_now_iso(),
            payload={
                "fork_group_id": fork_group.id,
                "join_node": fork_group.join_node_name,
            },
        ))
```

#### Finding the Join Node

```python
def _find_join_node(flow: Flow, fork_targets: tuple[str, ...]) -> str:
    """Find the join node for a set of fork targets.

    The join edge has join_sources matching the fork targets.
    """
    for edge in flow.edges:
        if edge.edge_type == EdgeType.JOIN and edge.join_sources is not None:
            if set(edge.join_sources) == set(fork_targets):
                assert edge.target is not None
                return edge.target
    raise ValueError(f"No join edge found for fork targets {fork_targets}")
```

#### Detecting Fork Edges

```python
def _is_fork(edges: list[Edge]) -> bool:
    """Check if any outgoing edge is a fork."""
    return any(e.edge_type == EdgeType.FORK for e in edges)
```

### Edge Cases
- **Fork with 1 target**: Valid but degenerate. Treated as a fork group with 1 member. Join triggers immediately after that member completes.
- **One fork member fails**: The fork group is NOT joined. The on_error policy applies to the failed task. Other fork members continue running unless the policy is `abort`.
- **Fork member fails, policy is skip**: The failed member is marked `skipped`. The fork group join check should treat `skipped` as equivalent to `completed` for the purposes of the "all members done" check.
- **Concurrent fork member completions**: Multiple members may complete near-simultaneously. The join check must be safe under concurrent access. Use the DB as the single source of truth — each completion queries the DB for all member statuses.
- **Nested forks**: The type checker should prevent this (not a supported topology). But if encountered, each fork-join pair is independent.
- **Fork edge context mode**: The type checker enforces that fork edges use `handoff` or `none` (not `session`). The executor trusts this but does not need to re-validate.
- **Budget exceeded during forked execution**: Let all currently running tasks finish, then pause. Do not enqueue the join target.

## Testing Strategy

Add to `tests/engine/test_executor.py`:

1. **test_fork_join_2_targets** — Flow: `entry -> [task_a, task_b] -> [task_a, task_b] -> merge -> exit`. Mock subprocess for all tasks. Verify:
   - Both `task_a` and `task_b` are created as pending
   - A fork_group record exists in DB with 2 members
   - `fork.started` event is emitted with both target names
   - After both complete, `merge` task is created
   - `fork.joined` event is emitted
   - Flow completes

2. **test_fork_join_3_targets** — Same as above but with 3 fork targets. Verify all 3 run and join correctly.

3. **test_fork_parallel_execution** — Fork into 2 tasks with `max_concurrent=4`. Verify both tasks are started before either completes (check event timestamps or start order).

4. **test_fork_semaphore_bounded** — Fork into 4 tasks with `max_concurrent=2`. Verify at most 2 tasks are running simultaneously. Use a mock subprocess that tracks concurrent execution count.

5. **test_fork_join_generation_tracking** — Verify fork members share the same generation as the source task. Verify the join target's generation is source generation + 1.

6. **test_fork_group_db_state** — After fork-join completes, verify:
   - fork_group status is `joined`
   - fork_group_members table has correct entries
   - fork_group.generation matches the fork members

7. **test_fork_join_context_aggregation** — Mock fork members to write SUMMARY.md. Verify the join task's prompt contains summaries from all members with proper headers.

8. **test_fork_member_failure** — One fork member fails (exit code 1). With `on_error=pause`: verify the flow pauses and the join does NOT trigger.

9. **test_fork_join_events** — Verify the complete event sequence: `fork.started`, `task.started` (x2), `task.completed` (x2), `fork.joined`, `task.started` (join), `task.completed` (join).

Use the same `MockSubprocessManager` and in-memory SQLite approach as ENGINE-005 tests.
