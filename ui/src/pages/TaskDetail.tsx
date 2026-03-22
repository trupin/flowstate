import { useState, useEffect, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api/client';
import type { QueuedTask } from '../api/types';
import './TaskDetail.css';

function formatTimestamp(iso: string | undefined | null): string {
  if (!iso) return '\u2014';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '\u2014';
  return d.toLocaleString();
}

function formatRelativeTime(iso: string | undefined | null): string {
  if (!iso) return '\u2014';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '\u2014';
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  if (diffMs < 0) return 'just now';

  const diffSecs = Math.floor(diffMs / 1000);
  if (diffSecs < 60) return `${diffSecs}s ago`;
  const diffMins = Math.floor(diffSecs / 60);
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

const STATUS_COLORS: Record<string, string> = {
  queued: 'var(--status-pending)',
  running: 'var(--status-running)',
  waiting: 'var(--status-waiting)',
  completed: 'var(--status-completed)',
  failed: 'var(--status-failed)',
  cancelled: 'var(--text-secondary)',
  paused: 'var(--status-paused)',
};

export function TaskDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const [task, setTask] = useState<QueuedTask | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTask = useCallback(() => {
    if (!taskId) return;
    api.tasks
      .get(taskId)
      .then((result) => {
        setTask(result);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load task');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [taskId]);

  // Fetch on mount and poll every 3s for active tasks
  useEffect(() => {
    fetchTask();
    const interval = setInterval(fetchTask, 3000);
    return () => clearInterval(interval);
  }, [fetchTask]);

  if (loading) {
    return <div className="task-detail-loading">Loading task...</div>;
  }

  if (error || !task) {
    return (
      <div className="task-detail-error">
        {error ?? 'Task not found'}
        <Link to="/" className="task-detail-back-link">
          Back to flows
        </Link>
      </div>
    );
  }

  const parsedParams = task.params_json
    ? (() => {
        try {
          return JSON.parse(task.params_json) as Record<string, unknown>;
        } catch {
          return null;
        }
      })()
    : null;

  const parsedOutput = task.output_json
    ? (() => {
        try {
          return JSON.parse(task.output_json) as Record<string, unknown>;
        } catch {
          return null;
        }
      })()
    : null;

  return (
    <div className="task-detail">
      <div className="task-detail-header">
        <div className="task-detail-breadcrumb">
          <Link to="/">Flows</Link>
          <span className="task-detail-breadcrumb-sep">/</span>
          <Link to={`/?flow=${task.flow_name}`}>{task.flow_name}</Link>
          <span className="task-detail-breadcrumb-sep">/</span>
          <span>Task</span>
        </div>

        <div className="task-detail-title-row">
          <h1 className="task-detail-title">{task.title}</h1>
          <span
            className="task-detail-status-badge"
            style={{
              background: STATUS_COLORS[task.status] ?? 'var(--text-secondary)',
            }}
          >
            {task.status}
          </span>
        </div>

        {task.description && (
          <p className="task-detail-description">{task.description}</p>
        )}
      </div>

      <div className="task-detail-body">
        {/* Metadata */}
        <section className="task-detail-section">
          <h3 className="task-detail-section-title">Details</h3>
          <div className="task-detail-grid">
            <span className="task-detail-key">ID</span>
            <span className="task-detail-value mono">{task.id}</span>

            <span className="task-detail-key">Flow</span>
            <span className="task-detail-value">{task.flow_name}</span>

            <span className="task-detail-key">Priority</span>
            <span className="task-detail-value">{task.priority}</span>

            {task.current_node && (
              <>
                <span className="task-detail-key">Current Node</span>
                <span className="task-detail-value mono">
                  {task.current_node}
                </span>
              </>
            )}

            <span className="task-detail-key">Created</span>
            <span className="task-detail-value">
              {formatTimestamp(task.created_at)}{' '}
              <span className="task-detail-relative">
                ({formatRelativeTime(task.created_at)})
              </span>
            </span>

            {task.started_at && (
              <>
                <span className="task-detail-key">Started</span>
                <span className="task-detail-value">
                  {formatTimestamp(task.started_at)}
                </span>
              </>
            )}

            {task.completed_at && (
              <>
                <span className="task-detail-key">Completed</span>
                <span className="task-detail-value">
                  {formatTimestamp(task.completed_at)}
                </span>
              </>
            )}

            {task.created_by && (
              <>
                <span className="task-detail-key">Created By</span>
                <span className="task-detail-value">{task.created_by}</span>
              </>
            )}

            {task.parent_task_id && (
              <>
                <span className="task-detail-key">Parent Task</span>
                <span className="task-detail-value">
                  <Link to={`/tasks/${task.parent_task_id}`}>
                    {task.parent_task_id.slice(0, 8)}...
                  </Link>
                </span>
              </>
            )}

            {task.flow_run_id && (
              <>
                <span className="task-detail-key">Flow Run</span>
                <span className="task-detail-value">
                  <Link to={`/runs/${task.flow_run_id}`}>
                    {task.flow_run_id.slice(0, 8)}...
                  </Link>
                </span>
              </>
            )}
          </div>
        </section>

        {/* Error */}
        {task.error_message && (
          <section className="task-detail-section">
            <h3 className="task-detail-section-title task-detail-error-title">
              Error
            </h3>
            <div className="task-detail-error-message">
              {task.error_message}
            </div>
          </section>
        )}

        {/* Input Params */}
        {parsedParams && Object.keys(parsedParams).length > 0 && (
          <section className="task-detail-section">
            <h3 className="task-detail-section-title">Input Parameters</h3>
            <pre className="task-detail-json">
              {JSON.stringify(parsedParams, null, 2)}
            </pre>
          </section>
        )}

        {/* Output */}
        {parsedOutput && Object.keys(parsedOutput).length > 0 && (
          <section className="task-detail-section">
            <h3 className="task-detail-section-title">Output</h3>
            <pre className="task-detail-json">
              {JSON.stringify(parsedOutput, null, 2)}
            </pre>
          </section>
        )}

        {/* Node History Timeline */}
        {task.history && task.history.length > 0 && (
          <section className="task-detail-section">
            <h3 className="task-detail-section-title">Node History</h3>
            <div className="task-detail-timeline">
              {task.history.map((entry) => (
                <div key={entry.id} className="task-timeline-entry">
                  <span className="task-timeline-dot" />
                  <span className="task-timeline-node mono">
                    {entry.node_name}
                  </span>
                  <span className="task-timeline-time">
                    {formatTimestamp(entry.started_at)}
                    {entry.completed_at &&
                      ` \u2192 ${formatTimestamp(entry.completed_at)}`}
                  </span>
                  {entry.flow_run_id && (
                    <Link
                      to={`/runs/${entry.flow_run_id}`}
                      className="task-timeline-run-link"
                    >
                      run: {entry.flow_run_id.slice(0, 8)}
                    </Link>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Child Tasks */}
        {task.children && task.children.length > 0 && (
          <section className="task-detail-section">
            <h3 className="task-detail-section-title">Child Tasks</h3>
            <div className="task-detail-children">
              {task.children.map((child) => (
                <Link
                  key={child.id}
                  to={`/tasks/${child.id}`}
                  className="task-child-item"
                >
                  <span
                    className="task-child-status-dot"
                    style={{
                      background:
                        STATUS_COLORS[child.status] ?? 'var(--text-secondary)',
                    }}
                  />
                  <span className="task-child-title">{child.title}</span>
                  <span className="task-child-status">{child.status}</span>
                </Link>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
