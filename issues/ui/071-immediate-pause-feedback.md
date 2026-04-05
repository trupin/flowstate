# [UI-071] Immediate pause feedback with pausing/resume UX

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-078
- Blocks: none

## Spec References
- specs.md Section 6.1 — "Flow Run Lifecycle"

## Summary

When the user clicks Pause, there is no visual feedback until the current node finishes executing (which can take minutes). The button should immediately swap to Resume and the status should show `pausing` to indicate the system acknowledged the request. Clicking Resume while pausing should cancel the pause and return to running. Clicking Resume while fully paused should start the next node as before.

## Acceptance Criteria
- [ ] `pausing` is added to the `FlowRunStatus` TypeScript union type
- [ ] When flow status is `pausing`, the ControlPanel shows the Resume button (not Pause)
- [ ] When flow status is `pausing`, the status label displays `pausing` (distinct from `paused`)
- [ ] Clicking Resume while `pausing` calls the resume API (which cancels the pause)
- [ ] Clicking Resume while `paused` calls the resume API (which starts next node)
- [ ] The `pausing` status appears in the sidebar active runs list with appropriate styling
- [ ] Cancel button remains available during `pausing` state

## Technical Design

### Files to Create/Modify
- `ui/src/api/types.ts` — Add `'pausing'` to `FlowRunStatus` union
- `ui/src/components/ControlPanel/ControlPanel.tsx` — Update `isPaused`/button logic to include `pausing`
- `ui/src/components/ControlPanel/ControlPanel.css` — Add styling for `pausing` status if needed
- `ui/src/components/Sidebar/Sidebar.tsx` — Ensure `pausing` flows show in active runs (if filtered by status)

### Key Implementation Details

**Type change** (types.ts):
```typescript
export type FlowRunStatus =
  | 'created'
  | 'running'
  | 'pausing'    // NEW
  | 'paused'
  | 'budget_exceeded'
  | 'completed'
  | 'failed'
  | 'cancelled';
```

**ControlPanel changes** (ControlPanel.tsx):
- Update `isPaused` to: `flowStatus === 'pausing' || flowStatus === 'paused' || flowStatus === 'budget_exceeded'`
- The `isActive` check already includes `isPaused`, so the Cancel button will automatically show during `pausing`
- The Resume button shows when `isPaused` is true, which now includes `pausing`

**Status label**: The existing `{flowStatus}` display (line 74) already renders the raw status string, so `pausing` will naturally appear as the label. Consider adding a CSS treatment (e.g., pulsing dot or different color) to distinguish `pausing` from `paused`.

**Sidebar**: Check if the sidebar filters active runs by specific status values. If so, add `pausing` to the filter list alongside `running` and `paused`.

### Edge Cases
- **Rapid status transitions**: The status may flash through `pausing` quickly if the node completes right after pause is clicked. The UI should handle this gracefully — React state updates will just show the latest status.
- **WebSocket reconnection**: If the WS disconnects during `pausing`, the reconnect replay should deliver both the `pausing` and `paused` events in order.
- **Budget exceeded while pausing**: If budget exceeds during `pausing`, the status transitions to `budget_exceeded`. The UI already shows Resume for that state.

## Testing Strategy
- **Component test**: Verify ControlPanel renders Resume button when `flowStatus='pausing'`
- **Component test**: Verify status label shows `pausing` when in that state
- **Build check**: `npm run build` must succeed with the new type (catches any unhandled switch cases)
- **Lint check**: `npm run lint` must pass

## E2E Verification Plan

### Verification Steps
1. Start server: `uv run flowstate serve` and UI: `cd ui && npm run dev`
2. Start a flow with a multi-step node
3. Click Pause while node is running
4. Verify: button immediately changes to Resume, status shows `pausing`
5. Wait for node to complete → verify status changes to `paused`, Resume button stays
6. Click Resume → verify flow continues
7. Repeat: start flow, click Pause, then immediately click Resume
8. Verify: status goes `pausing` → `running`, flow continues without interruption

## E2E Verification Log
_Filled in by the implementing agent as proof-of-work._

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
