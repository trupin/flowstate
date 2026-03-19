import { useRef, useEffect, useState } from 'react';
import type { LogEntry } from '../../api/types';
import './LogViewer.css';

export interface LogViewerProps {
  logs: LogEntry[];
  taskName?: string | null;
  onClear?: () => void;
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function LogViewer({ logs, taskName, onClear }: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  // Auto-scroll when pinned and new logs arrive
  useEffect(() => {
    if (pinned && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs, pinned]);

  // Reset pin state when task changes
  useEffect(() => {
    setPinned(true);
  }, [taskName]);

  // Detect manual scroll-up to auto-unpin
  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 30;
    if (!isAtBottom && pinned) {
      setPinned(false);
    }
  };

  if (!taskName) {
    return (
      <div className="log-viewer log-viewer-empty" data-testid="log-viewer">
        <span>Select a node to view logs</span>
      </div>
    );
  }

  return (
    <div className="log-viewer" data-testid="log-viewer">
      <div className="log-viewer-header">
        <span className="log-viewer-title">{taskName}</span>
        <div className="log-viewer-controls">
          <button
            className={`log-viewer-pin ${pinned ? 'active' : ''}`}
            onClick={() => {
              const newPinned = !pinned;
              setPinned(newPinned);
              if (newPinned && containerRef.current) {
                containerRef.current.scrollTop =
                  containerRef.current.scrollHeight;
              }
            }}
            title={pinned ? 'Auto-scroll ON' : 'Auto-scroll OFF'}
          >
            {pinned ? '\u2B07 Pinned' : '\u2B07 Unpinned'}
          </button>
          <button onClick={onClear} title="Clear logs (client-side only)">
            Clear
          </button>
        </div>
      </div>
      <div
        className="log-viewer-content"
        ref={containerRef}
        onScroll={handleScroll}
      >
        {logs.length === 0 ? (
          <div className="log-viewer-no-output">No output yet</div>
        ) : (
          logs.map((entry) => (
            <div
              key={entry.id}
              className={`log-line log-type-${entry.log_type}`}
            >
              <span className="log-timestamp">
                {formatTimestamp(entry.timestamp)}
              </span>
              <span className="log-content">{entry.content}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
