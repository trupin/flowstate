import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import type { TaskStatus } from '../api/types';
import './NodePill.css';

// --- Public data interface ---

export interface NodePillData {
  label: string;
  nodeType: 'entry' | 'task' | 'exit';
  status: TaskStatus;
  generation?: number;
  elapsedSeconds?: number;
  cwd?: string;
  taskDir?: string;
  worktreeDir?: string;
  hasExecution?: boolean;
  waitUntil?: string;
  isSelected?: boolean;
  [key: string]: unknown;
}

// --- Helpers ---

function truncateName(name: string, maxLen = 24): string {
  if (name.length <= maxLen) return name;
  return name.slice(0, maxLen - 1) + '\u2026';
}

// --- Node component ---

export function NodePill({ data }: NodeProps<Node<NodePillData>>) {
  const statusClass = `status-${data.status}`;
  const typeClass = `type-${data.nodeType}`;
  const selectedClass = data.isSelected ? 'node-pill-selected' : '';

  return (
    <div
      className={`node-pill ${statusClass} ${typeClass} ${selectedClass}`.trim()}
      data-testid={`node-${data.label}`}
      data-status={data.status}
      title={`${data.label} (${data.status})`}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="node-pill-handle"
      />

      <div className="node-pill-compact">
        {data.nodeType !== 'task' && (
          <span className="node-pill-type-indicator">
            {data.nodeType === 'entry' ? '\u25B6' : '\u25A0'}
          </span>
        )}
        <span className="node-pill-name">{truncateName(data.label)}</span>
        {(data.generation ?? 1) > 1 && (
          <span className="node-pill-generation">x{data.generation}</span>
        )}
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        className="node-pill-handle"
      />
    </div>
  );
}
