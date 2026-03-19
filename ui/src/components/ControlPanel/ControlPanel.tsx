import { useState } from 'react';
import type { FlowRunStatus, TaskStatus } from '../../api/types';
import './ControlPanel.css';

export interface ControlPanelProps {
  flowRunId: string;
  flowStatus: FlowRunStatus;
  elapsedSeconds: number;
  budgetSeconds: number;
  selectedTaskId?: string | null;
  selectedTaskStatus?: TaskStatus | null;
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
  onPause,
  onResume,
  onCancel,
  onRetry,
  onSkip,
}: ControlPanelProps) {
  const [pending, setPending] = useState<string | null>(null);

  const isRunning = flowStatus === 'running';
  const isPaused = flowStatus === 'paused' || flowStatus === 'budget_exceeded';
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
      <div className="control-panel-left">
        <span className="flow-status" data-testid="flow-status">
          {flowStatus}
        </span>
        <div className="control-panel-buttons">
          {isRunning && (
            <button
              data-testid="btn-pause"
              disabled={pending !== null}
              onClick={() => handleAction('pause', onPause)}
            >
              Pause
            </button>
          )}
          {isPaused && (
            <button
              data-testid="btn-resume"
              disabled={pending !== null}
              onClick={() => handleAction('resume', onResume)}
            >
              Resume
            </button>
          )}
          {isActive && (
            <button
              data-testid="btn-cancel"
              className="control-btn-danger"
              disabled={pending !== null}
              onClick={() => {
                if (window.confirm('Cancel this flow run?')) {
                  void handleAction('cancel', onCancel);
                }
              }}
            >
              Cancel
            </button>
          )}
          {hasFailedTask && (
            <>
              <button
                data-testid="btn-retry"
                disabled={pending !== null}
                onClick={() =>
                  handleAction('retry', () => onRetry(selectedTaskId))
                }
              >
                Retry Task
              </button>
              <button
                data-testid="btn-skip"
                disabled={pending !== null}
                onClick={() =>
                  handleAction('skip', () => onSkip(selectedTaskId))
                }
              >
                Skip Task
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
