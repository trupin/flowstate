# [UI-048] Fix node generation badges missing for some nodes in cycle flows

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md Section 8 — "Cycle Re-entry"

## Summary
In cycle flows (e.g., `discuss_flowstate.flow`), the "x2", "x3" generation badges on node pills are inconsistently displayed. In a completed run where all three nodes (moderator, alice, bob) executed multiple times, only Alice shows the x2 badge — Bob (also x2) and moderator (x3) show no badge. The root cause is in how the `tasks` Map in `useFlowRun.ts` is populated: it's keyed by `node_name`, meaning only one task execution per node is kept. The generation value depends on which execution ends up as the final entry, which can be wrong if WebSocket events or API fetch ordering causes a lower-generation execution to overwrite a higher one.

## Acceptance Criteria
- [ ] All nodes in a cycle flow show the correct generation badge (e.g., x2, x3) after the run completes
- [ ] Badges are correct on initial page load of a completed run (API fetch path)
- [ ] Badges are correct during live execution when watching a cycle flow in real-time (WebSocket path)
- [ ] Nodes executed only once show no badge (generation=1, no badge)

## Technical Design

### Files to Create/Modify
- `ui/src/hooks/useFlowRun.ts` — fix generation tracking
- `ui/src/pages/RunDetail.tsx` — potentially adjust `taskGenerations` derivation

### Key Implementation Details

The `tasks` Map in `useFlowRun.ts` is `Map<string, TaskExecution>` keyed by `node_name`. This loses multi-generation data. Two code paths need fixing:

**1. API fetch path (`fetchRunDetail`, line 265):**
```typescript
detail.tasks.forEach((t) => taskMap.set(t.node_name, t));
```
The API returns all task executions ordered by `created_at`. The last one for each node should have the highest generation. But if SQLite timestamp precision causes non-deterministic ordering, a gen-1 task could overwrite a gen-2 task. Fix: explicitly keep the highest generation:
```typescript
detail.tasks.forEach((t) => {
  const existing = taskMap.get(t.node_name);
  if (!existing || t.generation > existing.generation) {
    taskMap.set(t.node_name, t);
  }
});
```

**2. WebSocket event path (`task.completed`/`task.failed`, lines 107-153):**
```typescript
generation: existing?.generation ?? 1,
```
If the `existing` entry is missing (e.g., tasks Map was reset by a concurrent `fetchRunDetail`), generation defaults to 1. Fix: add generation to `task.completed` and `task.failed` backend events, or ensure the fallback is the correct generation from the DB.

### Edge Cases
- Nodes that only run once (generation=1) must NOT show a badge
- Fork-join nodes have separate generation tracking — verify those still work
- Page refresh mid-cycle should show correct badges for already-completed cycles

## Testing Strategy
- Manually verify with the reported run: `http://localhost:9090/runs/04d57aad-b729-4dbf-8260-fad1eeef1a70`
- Run a cycle flow (discuss_flowstate.flow) and verify all nodes show correct badges at completion
- Verify badges are correct after page refresh on a completed cycle run

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
