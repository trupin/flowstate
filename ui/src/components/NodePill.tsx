import { useState, useEffect } from 'react';
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
  waitUntil?: string;
  [key: string]: unknown;
}

// --- Helpers ---

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

function truncatePath(path: string, maxLen = 20): string {
  if (path.length <= maxLen) return path;
  return '...' + path.slice(-maxLen + 3);
}

function truncateName(name: string, maxLen = 24): string {
  if (name.length <= maxLen) return name;
  return name.slice(0, maxLen - 1) + '\u2026';
}

// --- Countdown timer for waiting nodes ---

function CountdownTimer({ until }: { until: string }) {
  const [remaining, setRemaining] = useState('');

  useEffect(() => {
    function update() {
      const diff = new Date(until).getTime() - Date.now();
      if (diff <= 0) {
        setRemaining('ready');
      } else {
        const secs = Math.floor(diff / 1000);
        const mins = Math.floor(secs / 60);
        setRemaining(mins > 0 ? `${mins}m ${secs % 60}s` : `${secs}s`);
      }
    }

    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [until]);

  return <span>{remaining}</span>;
}

// --- Node component ---

export function NodePill({ data }: NodeProps<Node<NodePillData>>) {
  const [expanded, setExpanded] = useState(false);

  const statusClass = `status-${data.status}`;
  const typeClass = `type-${data.nodeType}`;

  return (
    <div
      className={`node-pill ${statusClass} ${typeClass} ${expanded ? 'expanded' : ''}`}
      data-testid={`node-${data.label}`}
      data-status={data.status}
      onClick={() => {
        setExpanded(!expanded);
      }}
      title={!expanded ? `${data.label} (${data.status})` : undefined}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="node-pill-handle"
      />

      {/* Compact view (always visible) */}
      <div className="node-pill-compact">
        {data.nodeType !== 'task' && (
          <span className="node-pill-type-indicator">
            {data.nodeType === 'entry' ? '\u25B6' : '\u25A0'}
          </span>
        )}
        <span className="node-pill-name">
          {expanded ? data.label : truncateName(data.label)}
        </span>
        {(data.generation ?? 1) > 1 && (
          <span className="node-pill-generation">x{data.generation}</span>
        )}
      </div>

      {/* Expanded view (visible when expanded) */}
      {expanded && (
        <div className="node-pill-details">
          <span className="node-pill-type-badge">{data.nodeType}</span>
          {data.elapsedSeconds != null && (
            <span className="node-pill-elapsed">
              {formatElapsed(data.elapsedSeconds)}
            </span>
          )}
          {data.cwd && (
            <span className="node-pill-cwd" title={data.cwd}>
              {truncatePath(data.cwd)}
            </span>
          )}
          {data.status === 'waiting' && data.waitUntil && (
            <span className="node-pill-countdown">
              <CountdownTimer until={data.waitUntil} />
            </span>
          )}
        </div>
      )}

      <Handle
        type="source"
        position={Position.Bottom}
        className="node-pill-handle"
      />
    </div>
  );
}
