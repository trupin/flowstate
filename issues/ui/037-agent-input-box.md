# [UI-037] Always-visible input box + interrupt button in log viewer

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-014
- Blocks: —

## Spec References
- specs.md Section 10.5 — "Log Viewer"

## Summary
Add an input box at the bottom of the log viewer that is always visible while a task is running or interrupted. Add an interrupt button that stops the agent's current work so the user can interact. The interrupt button label/tooltip should make it clear that interrupting is for interaction, not cancellation. Sent messages appear in the log stream with a distinct "You" style. The input box is disabled after the task completes.

## Acceptance Criteria
- [ ] Input box visible at bottom of log viewer when selected task is `running` or `interrupted`
- [ ] Input box hidden/disabled when task is `completed`, `failed`, `pending`, `waiting`, `skipped`, or no task selected
- [ ] **Interrupt button** visible when task is `running` — labeled "Interrupt" with tooltip "Stop the agent to send a message"
- [ ] Interrupt button hidden when task is `interrupted` (already interrupted) or not running
- [ ] Clicking Interrupt calls `POST /api/runs/{run_id}/tasks/{task_id}/interrupt` and task status updates to `interrupted`
- [ ] Pressing Enter or clicking Send calls `POST /api/runs/{run_id}/tasks/{task_id}/message`
- [ ] Input clears after sending, refocuses for next message
- [ ] Loading/disabled state while sending (prevents double-send)
- [ ] Sent message immediately appears in log stream styled as user message (distinct background, "You" label)
- [ ] User input messages from WebSocket (`task.log` with `log_type: "user_input"`) render in the log stream for all clients
- [ ] When interrupted task receives a message and resumes, status updates back to `running` in the UI
- [ ] Error feedback: 409 → "Task is no longer running", inline below input

## Technical Design

### Files to Modify

- `ui/src/components/LogViewer/LogViewer.tsx` — Add input bar and interrupt button below `.log-viewer-content`. Input bar visible when `taskExecution?.status` is `running` or `interrupted`. Interrupt button visible only when `running`.

  **Critical rendering change**: `parseLogContent()` currently hides `user` events (line ~406: `return { kind: 'raw', text: '' }`). Add handling for `log_type === "user_input"` BEFORE the generic event type parsing. New `ParsedEntry` kind: `'user_input'` with message content. New rendering case in `LogEntryContent` with "You" label and distinct styling.

  **Visibility classification**: User input entries classified as `visible` (not noise/hidden).

- `ui/src/components/LogViewer/LogViewer.css` — Styles for:
  - `.log-viewer-input-bar` — sticky bottom, border-top, flex layout (input + buttons)
  - `.log-viewer-interrupt-btn` — distinct color (amber/yellow to convey "pause", not "danger")
  - `.log-entry-user-input` — distinct background (e.g., subtle blue/purple), "You" label styling

- `ui/src/api/client.ts` — Add:
  - `sendTaskMessage(runId, taskExecutionId, message): Promise<{status: string}>`
  - `interruptTask(runId, taskExecutionId): Promise<{status: string}>`

- `ui/src/pages/RunDetail.tsx` — Pass `runId` and `taskExecutionId` to LogViewer props.

- `ui/src/hooks/useFlowRun.ts` — Handle `task.interrupted` WebSocket event in `applyEvent`:
  - Update task status to `interrupted` in the tasks Map

- `ui/src/api/types.ts` — `TaskStatus` already includes `'interrupted'` (from STATE-009).

### Key Implementation Details

**Input bar layout:**
```tsx
{(taskExecution?.status === 'running' || taskExecution?.status === 'interrupted') && (
  <div className="log-viewer-input-bar">
    {taskExecution.status === 'running' && (
      <button
        className="log-viewer-interrupt-btn"
        onClick={handleInterrupt}
        disabled={interrupting}
        title="Stop the agent to send a message"
      >
        Interrupt
      </button>
    )}
    <input
      type="text"
      value={inputValue}
      onChange={(e) => setInputValue(e.target.value)}
      onKeyDown={(e) => e.key === 'Enter' && !sending && handleSend()}
      placeholder={taskExecution.status === 'interrupted'
        ? "Send a message to resume the agent..."
        : "Send a message to the agent..."}
      disabled={sending}
    />
    <button onClick={handleSend} disabled={sending || !inputValue.trim()}>
      Send
    </button>
  </div>
)}
```

**User input log entry rendering:**
```tsx
case 'user_input':
  return (
    <div className="log-entry-user-input">
      <span className="log-entry-user-label">You</span>
      <span className="log-entry-user-message">{entry.message}</span>
    </div>
  );
```

### Edge Cases
- Task completes while user is typing → input bar disappears; if Enter pressed at same moment, API returns 409, show error briefly
- Rapid interrupt + send → interrupt resolves first, then send resumes agent
- Multiple messages before agent processes → all queued, agent gets them in one batch
- Optimistic rendering + WebSocket event → dedup by checking if message already in log list
- Network error on send → show error inline, keep message in input for retry
- Network error on interrupt → show error, button re-enables

## Regression Risks
- `parseLogContent()` has ~170 lines of switching logic. New `user_input` case must be added carefully.
- The existing `eventType === 'user'` filter hides user turns — the new `log_type === 'user_input'` check must happen at a higher level (check `log_type` field before parsing `content` as JSON event).
- `TaskStatus` type change (`'interrupted'` added) may affect exhaustiveness checks elsewhere in the UI.
- LogViewer is 800+ lines — adding ~60 lines for input bar is manageable but watch file size.

## Testing Strategy
- `cd ui && npm run lint && npm run build`
- E2E with Playwright (headless=False):
  - Start a flow, wait for a task to be running
  - Verify interrupt button and input box appear
  - Click Interrupt, verify task status changes to `interrupted` and interrupt button disappears
  - Type message and send, verify message appears in log stream with "You" styling
  - Verify task resumes (status back to `running`)
  - Wait for task to complete, verify input bar disappears
- Unit: verify `sendTaskMessage` and `interruptTask` API functions
- Unit: verify `parseLogContent` handles `user_input` log type
- Unit: verify `applyEvent` handles `task.interrupted` event

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
