# [ENGINE-024] Emit executor activity logs visible in the UI console

## Domain
engine

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-021
- Blocks: none

## Summary
With the orchestrator removed, there are no longer logs showing the executor's decision-making process (which node is being dispatched, which edge was taken, judge results, fork/join coordination). The executor emits FlowEvents for these, but they appear only as structured WebSocket events — not as human-readable log entries in the UI's log console. Add a mechanism to emit human-readable activity log entries so users can follow the flow's progress in the UI without inspecting raw events.

## Acceptance Criteria
- [ ] Executor emits human-readable log entries for key decisions: node dispatch, edge transition, judge evaluation, fork/join, pause/resume
- [ ] These logs are stored in the DB (task_logs or a new table) and available via API
- [ ] The UI log console displays these orchestration-level logs alongside task logs
- [ ] Logs are clearly distinguished from task agent output (e.g., different styling or prefix)

## Technical Design

### Files to Modify
- `src/flowstate/engine/executor.py` — emit system log entries at key decision points
- `src/flowstate/server/routes.py` — ensure API serves these logs (may already work via task_logs)
- `ui/src/components/LogViewer/LogViewer.tsx` — render orchestration logs with distinct styling

### Key Implementation Details
- Use existing `self._db.insert_task_log()` with `log_type="system"` at key points:
  - "Dispatching node X (generation N)"
  - "Edge transition: X → Y (unconditional/conditional)"
  - "Judge decided: X → Y (confidence: 0.95, reasoning: ...)"
  - "Fork: X → [A, B, C]"
  - "Join: [A, B, C] → merge"
  - "Flow paused: reason"
- These can be inserted as logs on the task that triggered them
- The FlowEvent system already emits structured events — this is about adding human-readable text versions

### Edge Cases
- Logs for fork/join should be associated with the fork source task or join target task
- System logs should not interfere with the task's own log stream

## Testing Strategy
- Run a flow and verify activity logs appear in the UI console
- Check that existing task logs still render correctly alongside new system logs
