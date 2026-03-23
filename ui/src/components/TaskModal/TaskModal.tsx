import { useState, useEffect, useCallback } from 'react';
import { api } from '../../api/client';
import type { QueuedTask, FlowParam } from '../../api/types';
import './TaskModal.css';

interface TaskModalProps {
  flowName: string;
  flowParams?: FlowParam[];
  task?: QueuedTask | null;
  onClose: () => void;
  onSubmit: () => void;
}

export function TaskModal({
  flowName,
  flowParams,
  task,
  onClose,
  onSubmit,
}: TaskModalProps) {
  const [title, setTitle] = useState(task?.title ?? '');
  const [description, setDescription] = useState(task?.description ?? '');
  const [params, setParams] = useState<Record<string, string>>({});
  const [scheduleType, setScheduleType] = useState<
    'immediate' | 'scheduled' | 'recurring'
  >('immediate');
  const [scheduledAt, setScheduledAt] = useState('');
  const [cronExpression, setCronExpression] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Initialize params from task's existing params_json
  useEffect(() => {
    if (task?.params_json) {
      try {
        const parsed: unknown = JSON.parse(task.params_json);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          const stringified: Record<string, string> = {};
          for (const [key, value] of Object.entries(
            parsed as Record<string, unknown>,
          )) {
            stringified[key] = String(value ?? '');
          }
          setParams(stringified);
        }
      } catch {
        // ignore parse errors
      }
    }
  }, [task?.params_json]);

  // Close on Escape
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    },
    [onClose],
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;

    setSubmitting(true);
    setError(null);

    // Build params object, converting numeric strings to numbers where applicable
    const parsedParams: Record<string, unknown> = {};
    if (flowParams) {
      for (const p of flowParams) {
        const raw = params[p.name];
        if (raw !== undefined && raw !== '') {
          if (p.type === 'number') {
            parsedParams[p.name] = Number(raw);
          } else if (p.type === 'bool') {
            parsedParams[p.name] = raw === 'true';
          } else {
            parsedParams[p.name] = raw;
          }
        }
      }
    }
    // Include any extra params not in flowParams
    for (const [key, value] of Object.entries(params)) {
      if (!(key in parsedParams) && value !== '') {
        parsedParams[key] = value;
      }
    }

    try {
      if (task) {
        await api.tasks.update(task.id, {
          title: title.trim(),
          description: description.trim() || undefined,
          params:
            Object.keys(parsedParams).length > 0 ? parsedParams : undefined,
        });
      } else {
        const submitData: {
          title: string;
          description?: string;
          params?: Record<string, unknown>;
          scheduled_at?: string;
          cron?: string;
        } = {
          title: title.trim(),
          description: description.trim() || undefined,
          params:
            Object.keys(parsedParams).length > 0 ? parsedParams : undefined,
        };
        if (scheduleType === 'scheduled' && scheduledAt) {
          submitData.scheduled_at = new Date(scheduledAt).toISOString();
        }
        if (scheduleType === 'recurring' && cronExpression) {
          submitData.cron = cronExpression;
        }
        await api.tasks.submit(flowName, submitData);
      }
      onSubmit();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save task');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="task-modal-backdrop" onClick={onClose}>
      <div
        className="task-modal-content"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="task-modal-header">
          <h2>{task ? 'Edit Task' : 'Add Task'}</h2>
          <button className="task-modal-close-btn" onClick={onClose}>
            &times;
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="task-modal-fields">
            <div className="task-modal-field">
              <label className="task-modal-label">
                Title <span className="task-modal-required">*</span>
              </label>
              <input
                className="task-modal-input"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Task title"
                required
                autoFocus
              />
            </div>

            <div className="task-modal-field">
              <label className="task-modal-label">Description</label>
              <textarea
                className="task-modal-textarea"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Optional description"
                rows={3}
              />
            </div>

            {flowParams && flowParams.length > 0 && (
              <div className="task-modal-params-section">
                <label className="task-modal-label">Parameters</label>
                {flowParams.map((p) => (
                  <div key={p.name} className="task-modal-param">
                    <label className="task-modal-param-label">
                      {p.name}
                      <span className="task-modal-param-type">{p.type}</span>
                    </label>
                    <input
                      className="task-modal-input"
                      type={p.type === 'number' ? 'number' : 'text'}
                      value={params[p.name] ?? ''}
                      placeholder={
                        p.default_value !== undefined
                          ? String(p.default_value)
                          : `Enter ${p.name}`
                      }
                      onChange={(e) =>
                        setParams((prev) => ({
                          ...prev,
                          [p.name]: e.target.value,
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
            )}

            {!task && (
              <div className="task-modal-field">
                <label className="task-modal-label">When</label>
                <div className="task-modal-schedule-options">
                  <label>
                    <input
                      type="radio"
                      name="schedule"
                      value="immediate"
                      checked={scheduleType === 'immediate'}
                      onChange={() => setScheduleType('immediate')}
                    />
                    Immediate
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="schedule"
                      value="scheduled"
                      checked={scheduleType === 'scheduled'}
                      onChange={() => setScheduleType('scheduled')}
                    />
                    Schedule for
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="schedule"
                      value="recurring"
                      checked={scheduleType === 'recurring'}
                      onChange={() => setScheduleType('recurring')}
                    />
                    Recurring
                  </label>
                </div>

                {scheduleType === 'scheduled' && (
                  <input
                    type="datetime-local"
                    className="task-modal-input"
                    value={scheduledAt}
                    onChange={(e) => setScheduledAt(e.target.value)}
                  />
                )}

                {scheduleType === 'recurring' && (
                  <div>
                    <input
                      type="text"
                      className="task-modal-input"
                      placeholder="0 9 * * 1-5 (weekdays at 9am)"
                      value={cronExpression}
                      onChange={(e) => setCronExpression(e.target.value)}
                    />
                    <div className="task-modal-help">
                      Examples: <code>0 9 * * *</code> (daily 9am),{' '}
                      <code>*/30 * * * *</code> (every 30min)
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {error && <div className="task-modal-error">{error}</div>}

          <div className="task-modal-actions">
            <button
              type="button"
              onClick={onClose}
              className="task-modal-btn-cancel"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !title.trim()}
              className="task-modal-btn-submit"
            >
              {submitting ? 'Saving...' : task ? 'Save' : 'Add to Queue'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
