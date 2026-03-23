import { useState, useEffect, useCallback } from 'react';
import { api } from '../../api/client';
import { TaskModal } from '../TaskModal';
import type { QueuedTask, FlowParam } from '../../api/types';
import './TaskQueuePanel.css';

interface TaskQueuePanelProps {
  flowName: string;
  flowParams?: FlowParam[];
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

export function TaskQueuePanel({ flowName, flowParams }: TaskQueuePanelProps) {
  const [tasks, setTasks] = useState<QueuedTask[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [editingTask, setEditingTask] = useState<QueuedTask | null>(null);

  const fetchTasks = useCallback(() => {
    api.tasks
      .list(flowName)
      .then(setTasks)
      .catch(() => {
        // silently ignore fetch errors
      });
  }, [flowName]);

  // Fetch tasks on mount and poll every 3s
  useEffect(() => {
    fetchTasks();
    const interval = setInterval(fetchTasks, 3000);
    return () => clearInterval(interval);
  }, [fetchTasks]);

  const running = tasks.filter((t) => t.status === 'running');
  const scheduled = tasks.filter((t) => t.status === 'scheduled');
  const queued = tasks.filter((t) => t.status === 'queued');
  const completed = tasks
    .filter((t) => TERMINAL_STATUSES.has(t.status))
    .slice(0, 5);

  const handleAdd = () => {
    setEditingTask(null);
    setShowModal(true);
  };

  const handleEdit = (task: QueuedTask) => {
    setEditingTask(task);
    setShowModal(true);
  };

  const handleRemove = (taskId: string) => {
    api.tasks
      .remove(taskId)
      .then(fetchTasks)
      .catch(() => {
        // silently ignore remove errors
      });
  };

  const handleCancel = (taskId: string) => {
    api.tasks
      .cancel(taskId)
      .then(fetchTasks)
      .catch(() => {
        // silently ignore cancel errors
      });
  };

  const handleModalClose = () => {
    setShowModal(false);
    setEditingTask(null);
  };

  const handleModalSubmit = () => {
    setShowModal(false);
    setEditingTask(null);
    fetchTasks();
  };

  return (
    <section className="task-queue-panel">
      <div className="task-queue-header">
        <h3 className="task-queue-title">Task Queue</h3>
        <button className="task-queue-add-btn" onClick={handleAdd}>
          + Add
        </button>
      </div>

      {running.length > 0 && (
        <div className="task-queue-group">
          <div className="task-queue-group-label">Running</div>
          {running.map((t) => (
            <div key={t.id} className="task-item task-running">
              <span className="task-status-indicator running" />
              <span className="task-item-title">{t.title}</span>
              {t.current_node && (
                <span className="task-item-node">node: {t.current_node}</span>
              )}
              <button
                className="task-action-btn task-cancel-btn"
                onClick={() => handleCancel(t.id)}
                title="Cancel task"
              >
                &times;
              </button>
            </div>
          ))}
        </div>
      )}

      {scheduled.length > 0 && (
        <div className="task-queue-group">
          <div className="task-queue-group-label">Scheduled</div>
          {scheduled.map((t) => (
            <div key={t.id} className="task-item task-scheduled">
              <span className="task-status-indicator scheduled" />
              <span className="task-item-title">{t.title}</span>
              {t.scheduled_at && (
                <span className="task-scheduled-at">
                  {new Date(t.scheduled_at).toLocaleString()}
                </span>
              )}
              <button
                className="task-action-btn task-remove-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRemove(t.id);
                }}
                title="Remove task"
              >
                &times;
              </button>
            </div>
          ))}
        </div>
      )}

      {queued.length > 0 && (
        <div className="task-queue-group">
          <div className="task-queue-group-label">Queued</div>
          {queued.map((t) => (
            <div key={t.id} className="task-item task-queued">
              <span className="task-status-indicator queued" />
              <span className="task-item-title">{t.title}</span>
              {t.cron_expression && (
                <span className="task-cron">{t.cron_expression}</span>
              )}
              <button
                className="task-action-btn task-edit-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  handleEdit(t);
                }}
                title="Edit task"
              >
                &#9998;
              </button>
              <button
                className="task-action-btn task-remove-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRemove(t.id);
                }}
                title="Remove task"
              >
                &times;
              </button>
            </div>
          ))}
        </div>
      )}

      {completed.length > 0 && (
        <div className="task-queue-group">
          <div className="task-queue-group-label">Recent</div>
          {completed.map((t) => (
            <div key={t.id} className="task-item task-completed-item">
              <span
                className={`task-status-indicator ${t.status === 'completed' ? 'completed' : 'failed'}`}
              />
              <span className="task-item-title">{t.title}</span>
              <span className="task-item-status">{t.status}</span>
            </div>
          ))}
        </div>
      )}

      {tasks.length === 0 && (
        <div className="task-queue-empty">
          No tasks in queue. Click &quot;+ Add&quot; to submit a task.
        </div>
      )}

      {showModal && (
        <TaskModal
          flowName={flowName}
          flowParams={flowParams}
          task={editingTask}
          onClose={handleModalClose}
          onSubmit={handleModalSubmit}
        />
      )}
    </section>
  );
}
