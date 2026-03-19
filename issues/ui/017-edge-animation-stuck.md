# [UI-017] Edge Animation Persists After State Transition

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-004 (Graph Visualization)
- Blocks: —

## Spec References
- specs.md Section 10 — "Web Interface"

## Summary
When a flow run transitions from one node to the next, the animated edge (showing active data flow) on the previous edge remains animated even after the state has moved to the next edge. This is visually misleading — it makes it look like the previous transition is still active when it has already completed. Edges should stop animating once their transition is complete and the target node has started executing.

## Current Behavior
1. Task A starts → edge A→B animates (correct)
2. Task A completes, task B starts → edge A→B **still animates** (bug)
3. Task B completes, task C starts → edges A→B and B→C **both animate** (bug compounds)

Over time, all traversed edges end up with active animation, making it impossible to tell which transition is currently active.

## Desired Behavior
1. Task A starts → no edge animated (task is running, no transition yet)
2. Task A completes, edge evaluation begins → edge A→B animates briefly
3. Task B starts → edge A→B stops animating (shows as "completed" style: solid, maybe slightly highlighted), task B node shows running state
4. Only the edge currently being traversed (or most recently traversed) should animate

## Acceptance Criteria
- [ ] Edges stop animating once the target node starts executing
- [ ] Only the currently active transition (if any) has animation
- [ ] Completed edges show a distinct "traversed" style (not animated, but visually different from untraversed edges)
- [ ] Edge animation state is derived from the flow run state (task statuses + edge transitions), not accumulated
- [ ] Works correctly with conditional edges (only the chosen edge shows as traversed)
- [ ] Works correctly with fork edges (all fork edges animate simultaneously, then stop when targets start)

## Technical Design

### Files to Modify
- `ui/src/components/FlowGraph/` or equivalent — Edge rendering logic
- Graph state derivation — Where edge status is computed from run state

### Key Implementation Details

The root cause is likely that edge animation state is set when a transition occurs but never cleared. The fix should:

1. **Derive edge state from current run state** rather than accumulating animation flags
2. An edge should be "active/animated" only when:
   - An `edge.transition` event was received AND
   - The target task is not yet in `running` or `completed` state
3. An edge should be "traversed" (static, completed style) when:
   - An `edge.transition` event was received AND
   - The target task is in `running`, `completed`, or `failed` state
4. An edge should be "idle" (default style) when:
   - No transition has occurred on it yet

### Edge Cases
- Judge evaluation in progress (between task completion and edge transition) — no edge should animate yet
- Cycle re-entry — edge may animate multiple times
- Fork edges — all animate together, then all stop together
- Cancelled/paused flow — all animations should stop

## Testing Strategy
1. Linear flow: verify only one edge animates at a time
2. After flow completion: verify no edges are still animating
3. Fork: verify all fork edges animate together, then stop
4. Conditional: verify only the chosen edge shows as traversed
