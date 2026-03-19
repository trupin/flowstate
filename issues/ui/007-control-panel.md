# [UI-007] Control Panel (pause/resume/cancel/retry/skip + budget)

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: UI-002, UI-009
- Blocks: UI-011

## Spec References
- specs.md Section 10.6 — "Control Panel"
- agents/05-ui.md — "Control Panel (`ControlPanel.tsx`)"

## Summary
Create the control panel component that sits at the bottom of the Run Detail page. It provides flow control buttons (pause, resume, cancel, retry, skip) with conditional visibility based on flow/task status, and a budget progress bar that shows elapsed time versus budget with color changes at warning thresholds (75%, 90%, 95%).

## Acceptance Criteria
- [ ] All buttons include `data-testid` attributes: `btn-pause`, `btn-resume`, `btn-cancel`, `btn-retry`, `btn-skip`; flow status has `data-testid="flow-status"`, budget bar has `data-testid="budget-bar"` (required for E2E tests)
- [ ] `ui/src/components/ControlPanel.tsx` exists and renders control buttons + budget bar
- [ ] `ui/src/components/ControlPanel.css` exists with control panel styles
- [ ] **Pause** button: visible when flow status is `running`; sends pause action
- [ ] **Resume** button: visible when flow status is `paused` or `budget_exceeded`; calls `POST /api/runs/:id/resume`
- [ ] **Cancel** button: visible when flow status is `running` or `paused`; shows confirmation dialog before sending
- [ ] **Retry** button: visible when a failed task is selected; sends retry action for the selected task
- [ ] **Skip** button: visible when a failed task is selected; sends skip action for the selected task
- [ ] Budget bar: shows progress as `elapsed_seconds / budget_seconds`
- [ ] Budget bar color changes: default blue, yellow at >= 75%, orange at >= 90%, red at >= 95%
- [ ] Budget bar shows percentage text (e.g., "42%") and time remaining or elapsed
- [ ] Control panel has a fixed height at the bottom of the layout
- [ ] Buttons are disabled while an action is in-flight (prevent double-click)

## Technical Design

### Files to Create/Modify
- `ui/src/components/ControlPanel.tsx` — control panel component
- `ui/src/components/ControlPanel.css` — control panel styles

### Key Implementation Details

#### Props interface

```typescript
interface ControlPanelProps {
    flowRunId: string;
    flowStatus: FlowRunStatus;
    elapsedSeconds: number;
    budgetSeconds: number;
    selectedTaskId?: string | null;
    selectedTaskStatus?: TaskStatus | null;
    onPause: () => void;
    onResume: () => void;
    onCancel: () => void;
    onRetry: (taskId: string) => void;
    onSkip: (taskId: string) => void;
}
```

The parent (RunDetail) wires the `on*` callbacks to the appropriate API calls or WebSocket actions.

#### Component structure

```typescript
import { useState } from 'react';
import './ControlPanel.css';

export function ControlPanel({
    flowStatus, elapsedSeconds, budgetSeconds,
    selectedTaskId, selectedTaskStatus,
    onPause, onResume, onCancel, onRetry, onSkip,
}: ControlPanelProps) {
    const [pending, setPending] = useState<string | null>(null);

    const isRunning = flowStatus === 'running';
    const isPaused = flowStatus === 'paused' || flowStatus === 'budget_exceeded';
    const isActive = isRunning || isPaused;
    const hasFailedTask = selectedTaskStatus === 'failed' && selectedTaskId;

    const percent = budgetSeconds > 0
        ? Math.min(100, Math.round((elapsedSeconds / budgetSeconds) * 100))
        : 0;

    const budgetColor =
        percent >= 95 ? 'var(--error)' :
        percent >= 90 ? 'var(--status-skipped)' :  // orange
        percent >= 75 ? 'var(--warning)' :
        'var(--accent)';

    async function handleAction(name: string, fn: () => void | Promise<void>) {
        setPending(name);
        try {
            await fn();
        } finally {
            setPending(null);
        }
    }

    return (
        <div className="control-panel">
            <div className="control-panel-buttons">
                {isRunning && (
                    <button
                        disabled={pending !== null}
                        onClick={() => handleAction('pause', onPause)}
                    >
                        Pause
                    </button>
                )}
                {isPaused && (
                    <button
                        disabled={pending !== null}
                        onClick={() => handleAction('resume', onResume)}
                    >
                        Resume
                    </button>
                )}
                {isActive && (
                    <button
                        className="control-btn-danger"
                        disabled={pending !== null}
                        onClick={() => {
                            if (window.confirm('Cancel this flow run?')) {
                                handleAction('cancel', onCancel);
                            }
                        }}
                    >
                        Cancel
                    </button>
                )}
                {hasFailedTask && (
                    <>
                        <button
                            disabled={pending !== null}
                            onClick={() => handleAction('retry', () => onRetry(selectedTaskId!))}
                        >
                            Retry Task
                        </button>
                        <button
                            disabled={pending !== null}
                            onClick={() => handleAction('skip', () => onSkip(selectedTaskId!))}
                        >
                            Skip Task
                        </button>
                    </>
                )}
            </div>

            <div className="control-panel-budget">
                <div className="budget-bar">
                    <div
                        className="budget-bar-fill"
                        style={{ width: `${percent}%`, backgroundColor: budgetColor }}
                    />
                </div>
                <span className="budget-label">
                    Budget: {formatDuration(elapsedSeconds)} / {formatDuration(budgetSeconds)} ({percent}%)
                </span>
            </div>
        </div>
    );
}
```

#### CSS

```css
.control-panel {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    height: var(--control-panel-height);
    background: var(--bg-secondary);
    border-top: 1px solid var(--border);
    gap: 16px;
}

.control-panel-buttons {
    display: flex;
    gap: 8px;
}

.control-panel-buttons button {
    font-size: 12px;
    padding: 5px 14px;
    border-radius: 4px;
}

.control-btn-danger {
    border-color: var(--error);
    color: var(--error);
}

.control-btn-danger:hover {
    background: var(--error);
    color: #fff;
}

.control-panel-budget {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
}

.budget-bar {
    width: 200px;
    height: 8px;
    background: var(--bg-tertiary);
    border-radius: 4px;
    overflow: hidden;
}

.budget-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.3s ease, background-color 0.3s ease;
}

.budget-label {
    font-size: 12px;
    color: var(--text-secondary);
    white-space: nowrap;
}
```

#### Helper function

```typescript
function formatDuration(totalSeconds: number): string {
    const hours = Math.floor(totalSeconds / 3600);
    const mins = Math.floor((totalSeconds % 3600) / 60);
    const secs = Math.round(totalSeconds % 60);
    if (hours > 0) return `${hours}h ${mins}m`;
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
}
```

#### Confirmation dialog

The Cancel button uses `window.confirm()` for simplicity. A custom modal can be added in Phase 3 if desired, but for MVP the native browser dialog is sufficient.

#### Action flow

- **Pause**: Parent sends WebSocket action `{ action: "pause", flow_run_id }` via `useWebSocket.send()`
- **Resume**: Parent calls REST `POST /api/runs/:id/resume` via `api.runs.resume(id)`
- **Cancel**: Parent sends WebSocket action `{ action: "cancel", flow_run_id }` or REST `POST /api/runs/:id/cancel`
- **Retry**: Parent sends WebSocket action `{ action: "retry_task", flow_run_id, payload: { task_execution_id } }` or REST `POST /api/runs/:id/tasks/:taskId/retry`
- **Skip**: Parent sends WebSocket action `{ action: "skip_task", flow_run_id, payload: { task_execution_id } }` or REST `POST /api/runs/:id/tasks/:taskId/skip`

### Edge Cases
- Flow is `completed` / `failed` / `cancelled` — no buttons visible (all guards fail)
- Budget is 0 or not set — budget bar shows 0%, handle division by zero
- Elapsed exceeds budget — cap at 100%, show red
- No task selected but flow has failed tasks — retry/skip buttons are hidden (require explicit selection)
- Rapid button clicks — `pending` state disables all buttons while an action is in-flight
- `budget_exceeded` status — Resume button is visible (user can add budget and resume)

## Testing Strategy
Minimal for MVP:
1. Component renders without crashing with `running` status
2. Correct buttons are visible for each flow status combination
3. Budget bar renders with correct width percentage
4. Visual verification: color changes at 75%, 90%, 95% thresholds
