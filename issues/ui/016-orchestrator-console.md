# [UI-016] Orchestrator Console in Run Detail

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: UI-011 (Run Detail Page), ENGINE-015, ENGINE-016
- Blocks: —

## Spec References
- specs.md Section 9.6 — "Orchestrator Agents"
- specs.md Section 10 — "Web Interface"

## Summary
When a flow run uses orchestrator agents (ENGINE-015/016), the orchestrator's own logs (session init, task instructions, judge evaluations) are invisible to the user — they only see the subagent's output in the task log viewer. Add an "Orchestrator" button/tab to the Run Detail page that opens a console showing the orchestrator's conversation history. This lets users see the coordination layer: what instructions the orchestrator received, how it spawned subagents, and how it evaluated judge decisions.

## Current Behavior
- The Run Detail page shows task logs when a node is clicked
- Orchestrator sessions run behind the scenes — their output is streamed through the executor but not surfaced distinctly
- Users have no way to see the orchestrator's reasoning or coordination decisions
- The orchestrator's system prompt, task instructions, and judge instructions are invisible

## Desired Behavior
- An "Orchestrator" button appears in the Run Detail header/control bar when orchestrator sessions exist for the run
- Clicking it opens a panel (replacing or alongside the log viewer) showing the orchestrator's conversation history
- The console shows:
  1. The orchestrator's system prompt (collapsible, shown at top)
  2. Each resume instruction sent to the orchestrator (task execution, judge evaluation)
  3. The orchestrator's responses (subagent spawning, judge decisions)
  4. Timestamps for each interaction
- If multiple orchestrators exist (multiple cwds), a selector lets the user switch between them
- The orchestrator console streams live during execution (same WebSocket mechanism as task logs)

## Acceptance Criteria
- [ ] "Orchestrator" button visible in Run Detail when orchestrator sessions exist
- [ ] Clicking the button shows the orchestrator console panel
- [ ] Console displays orchestrator conversation history in chronological order
- [ ] System prompt shown at top (collapsible, collapsed by default)
- [ ] Each resume interaction shown as a distinct block: instruction → response
- [ ] Task execution instructions show the task name and INPUT.md path
- [ ] Judge instructions show the node name and available targets
- [ ] Live streaming of orchestrator output during active runs
- [ ] Multiple orchestrators: selector/tabs to switch between them (keyed by cwd)
- [ ] Console reuses the LogViewer styling for consistency
- [ ] Button is hidden when no orchestrator sessions exist (backward compat with direct subprocess runs)

## Technical Design

### Files to Create/Modify
- `ui/src/components/OrchestratorConsole/OrchestratorConsole.tsx` — New component
- `ui/src/components/OrchestratorConsole/OrchestratorConsole.css` — Styles
- `ui/src/components/OrchestratorConsole/index.ts` — Export
- `ui/src/pages/RunDetail.tsx` — Add orchestrator button and console integration
- `ui/src/api/types.ts` — Add orchestrator session types
- `ui/src/api/client.ts` — Add API endpoint for orchestrator logs

### Backend Changes Required
The backend needs a new API endpoint to serve orchestrator data:

**New REST endpoint:**
- `GET /api/runs/:id/orchestrators` — List orchestrator sessions for a run
  - Response: `[{ session_id, harness, cwd, data_dir, is_initialized }]`
- `GET /api/runs/:id/orchestrators/:session_id/logs` — Get orchestrator session logs
  - Response: same format as task logs

**New WebSocket event:**
- `orchestrator.log` — Streams orchestrator output in real-time
  - Payload: `{ session_id, log_type, content }`

These backend changes need a corresponding SERVER issue (or can be included in this issue if kept small).

### Key Implementation Details

#### Orchestrator data source

Option A (file-based, simpler): Read orchestrator data from the filesystem:
- System prompt: `~/.flowstate/runs/<run-id>/orchestrator/<key>/system_prompt.md`
- Session ID: `~/.flowstate/runs/<run-id>/orchestrator/<key>/session_id`
- Logs: filter task_logs by the orchestrator's session_id

Option B (API-based, better UX): Add the backend endpoints above. The orchestrator's stream events are already logged to the DB via the executor's event loop — they just need to be queryable separately.

#### OrchestratorConsole component

```typescript
interface OrchestratorConsoleProps {
  runId: string;
  orchestrators: OrchestratorInfo[];
  selectedOrchestrator: string | null;
  onSelect: (sessionId: string) => void;
}
```

- Header: orchestrator selector (if multiple) + cwd display
- System prompt section (collapsible)
- Chronological log of interactions
- Reuses LogViewer's parsed content rendering for the actual log lines

#### Run Detail integration

- Add state: `showOrchestrator: boolean`
- Add button in control bar: "Orchestrator" with a terminal/gear icon
- When toggled: replace the log viewer panel with OrchestratorConsole
- Or: split the right panel into tabs (Task Logs | Orchestrator)

### Edge Cases
- Run without orchestrator (direct subprocess mode) — button hidden
- Orchestrator session crashed mid-run — show logs up to crash, indicate error
- Multiple orchestrators — tab/selector UI, default to first
- Very long orchestrator conversations — virtual scrolling or pagination
- Run completed — show historical orchestrator logs from DB/files

## Testing Strategy
1. Component renders orchestrator conversation history
2. System prompt collapsible and collapsed by default
3. Multiple orchestrators show selector
4. Button hidden when no orchestrators exist
5. Live streaming works during active run
