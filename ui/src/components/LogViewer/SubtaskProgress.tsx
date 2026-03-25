import { useState } from 'react';
import type { Subtask } from '../../api/types';
import './SubtaskProgress.css';

export interface SubtaskProgressProps {
  subtasks: Subtask[];
  loading: boolean;
}

function statusIcon(status: Subtask['status']): string {
  switch (status) {
    case 'todo':
      return '\u25CB'; // ○
    case 'in_progress':
      return '\u25C9'; // ◉
    case 'done':
      return '\u2713'; // ✓
  }
}

export function SubtaskProgress({ subtasks, loading }: SubtaskProgressProps) {
  const [expanded, setExpanded] = useState(false);

  // Render nothing when there are no subtasks and we're not loading
  if (subtasks.length === 0 && !loading) {
    return null;
  }

  // Render nothing while loading with no prior data
  if (loading && subtasks.length === 0) {
    return null;
  }

  const doneCount = subtasks.filter((s) => s.status === 'done').length;
  const total = subtasks.length;
  const progressPct = total > 0 ? (doneCount / total) * 100 : 0;

  return (
    <div className="subtask-progress" data-testid="subtask-progress">
      <div
        className="subtask-progress-summary"
        onClick={() => setExpanded((prev) => !prev)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setExpanded((prev) => !prev);
          }
        }}
        aria-expanded={expanded}
      >
        <span className="subtask-progress-chevron">
          {expanded ? '\u25BE' : '\u25B8'}
        </span>
        <span className="subtask-progress-count">
          {doneCount}/{total} subtasks
        </span>
        <span className="subtask-progress-bar-container">
          <span
            className="subtask-progress-bar-fill"
            style={{ width: `${progressPct}%` }}
          />
        </span>
      </div>
      {expanded && (
        <div className="subtask-progress-list">
          {subtasks.map((subtask) => (
            <div
              key={subtask.id}
              className={`subtask-progress-item subtask-status-${subtask.status}`}
            >
              <span
                className={`subtask-progress-icon ${subtask.status === 'in_progress' ? 'subtask-icon-pulse' : ''}`}
              >
                {statusIcon(subtask.status)}
              </span>
              <span className="subtask-progress-title">{subtask.title}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
