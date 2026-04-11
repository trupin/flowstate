import { useState } from 'react';
import type { FlowRunStatus, TaskStatus } from '../../api/types';
import type { PendingAction } from '../../hooks/useFlowRun';
import './ControlPanel.css';

export interface ControlPanelProps {
  flowRunId: string;
  flowStatus: FlowRunStatus;
  elapsedSeconds: number;
  budgetSeconds: number;
  selectedTaskId?: string | null;
  selectedTaskStatus?: TaskStatus | null;
  /**
   * Current pending WebSocket control action. When set, the matching
   * button(s) show a pending state and are disabled until the hook
   * clears it (on ack, error, matching event, or timeout).
   */
  pendingAction?: PendingAction | null;
  onPause: () => void | Promise<void>;
  onResume: () => void | Promise<void>;
  onCancel: () => void | Promise<void>;
  onRetry: (taskId: string) => void | Promise<void>;
  onSkip: (taskId: string) => void | Promise<void>;
}

function formatDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const mins = Math.floor((totalSeconds % 3600) / 60);
  const secs = Math.round(totalSeconds % 60);
  if (hours > 0) return `${hours}h ${mins}m`;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
}

export function ControlPanel({
  flowStatus,
  elapsedSeconds,
  budgetSeconds,
  selectedTaskId,
  selectedTaskStatus,
  pendingAction,
  onPause,
  onResume,
  onCancel,
  onRetry,
  onSkip,
}: ControlPanelProps) {
  // Local pending only for Resume (which goes through REST, not WS).
  // All WebSocket actions (pause/cancel/retry_task/skip_task) are tracked
  // by the parent hook via ``pendingAction`` and cleared on ack/error/
  // matching event/timeout.
  const [localPending, setLocalPending] = useState<string | null>(null);

  const isRunning = flowStatus === 'running';
  const isPaused =
    flowStatus === 'pausing' ||
    flowStatus === 'paused' ||
    flowStatus === 'budget_exceeded';
  const isActive = isRunning || isPaused;
  const hasFailedTask = selectedTaskStatus === 'failed' && selectedTaskId;

  const percent =
    budgetSeconds > 0
      ? Math.min(100, Math.round((elapsedSeconds / budgetSeconds) * 100))
      : 0;

  const budgetColor =
    percent >= 95
      ? 'var(--error)'
      : percent >= 90
        ? 'var(--status-skipped)'
        : percent >= 75
          ? 'var(--warning)'
          : 'var(--accent)';

  async function handleLocal(name: string, fn: () => void | Promise<void>) {
    setLocalPending(name);
    try {
      await fn();
    } finally {
      setLocalPending(null);
    }
  }

  // A button is disabled if any action is in-flight -- either a local
  // REST action (resume) or a websocket action awaiting ack.
  const anyPending = pendingAction !== null && pendingAction !== undefined;
  const anyLocalPending = localPending !== null;
  const anyDisabled = anyPending || anyLocalPending;

  const pausePending = pendingAction?.action === 'pause';
  const cancelPending =
    pendingAction?.action === 'cancel' || pendingAction?.action === 'abort';
  const retryPending =
    pendingAction?.action === 'retry_task' &&
    (pendingAction.task_execution_id === undefined ||
      pendingAction.task_execution_id === selectedTaskId);
  const skipPending =
    pendingAction?.action === 'skip_task' &&
    (pendingAction.task_execution_id === undefined ||
      pendingAction.task_execution_id === selectedTaskId);

  return (
    <div className="control-panel">
      <div className="control-panel-left">
        <span className="flow-status" data-testid="flow-status">
          {flowStatus}
        </span>
        <div className="control-panel-buttons">
          {isRunning && (
            <button
              data-testid="btn-pause"
              data-pending={pausePending || undefined}
              disabled={anyDisabled}
              onClick={() => void onPause()}
            >
              {pausePending ? 'Pausing...' : 'Pause'}
            </button>
          )}
          {isPaused && (
            <button
              data-testid="btn-resume"
              disabled={anyDisabled}
              onClick={() => handleLocal('resume', onResume)}
            >
              {localPending === 'resume' ? 'Resuming...' : 'Resume'}
            </button>
          )}
          {isActive && (
            <button
              data-testid="btn-cancel"
              data-pending={cancelPending || undefined}
              className="control-btn-danger"
              disabled={anyDisabled}
              onClick={() => {
                if (window.confirm('Cancel this flow run?')) {
                  void onCancel();
                }
              }}
            >
              {cancelPending ? 'Cancelling...' : 'Cancel'}
            </button>
          )}
          {hasFailedTask && (
            <>
              <button
                data-testid="btn-retry"
                data-pending={retryPending || undefined}
                disabled={anyDisabled}
                onClick={() => onRetry(selectedTaskId)}
              >
                {retryPending ? 'Retrying...' : 'Retry Task'}
              </button>
              <button
                data-testid="btn-skip"
                data-pending={skipPending || undefined}
                disabled={anyDisabled}
                onClick={() => onSkip(selectedTaskId)}
              >
                {skipPending ? 'Skipping...' : 'Skip Task'}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="control-panel-budget">
        <div className="budget-bar" data-testid="budget-bar">
          <div
            className="budget-bar-fill"
            style={{
              width: `${percent}%`,
              backgroundColor: budgetColor,
            }}
          />
        </div>
        <span className="budget-label">
          Budget: {formatDuration(elapsedSeconds)} /{' '}
          {formatDuration(budgetSeconds)} ({percent}%)
        </span>
      </div>
    </div>
  );
}
