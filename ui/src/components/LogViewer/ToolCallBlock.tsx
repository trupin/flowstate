import { useState, useMemo } from 'react';
import './ToolCallBlock.css';

export interface ToolCallBlockProps {
  toolName: string;
  input: Record<string, unknown>;
  result: string | null;
  timestamp: string;
}

const PRIMARY_ARG_PRIORITY = [
  'file_path',
  'command',
  'pattern',
  'query',
  'prompt',
  'description',
];

function truncateStr(s: string, maxLen: number): string {
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen - 3) + '...';
}

function getPrimarySummary(input: Record<string, unknown>): string {
  // Try priority keys first
  for (const key of PRIMARY_ARG_PRIORITY) {
    if (key in input && typeof input[key] === 'string') {
      return `${key}="${truncateStr(input[key] as string, 80)}"`;
    }
  }
  // Fall back to first string-valued arg
  for (const [key, value] of Object.entries(input)) {
    if (typeof value === 'string') {
      return `${key}="${truncateStr(value, 80)}"`;
    }
  }
  return '';
}

function formatParamValue(value: unknown): string {
  if (typeof value === 'string') {
    return truncateStr(value, 500);
  }
  if (value === null || value === undefined) {
    return String(value);
  }
  const serialized = JSON.stringify(value, null, 2);
  return truncateStr(serialized, 500);
}

export function ToolCallBlock(props: ToolCallBlockProps) {
  const { toolName, input, result } = props;
  const [expanded, setExpanded] = useState(false);

  const summary = useMemo(() => getPrimarySummary(input), [input]);
  const paramEntries = useMemo(() => Object.entries(input), [input]);

  const handleToggle = () => {
    setExpanded((prev) => !prev);
  };

  return (
    <div
      className={`tool-call-block${expanded ? ' expanded' : ''}`}
      data-testid="tool-call-block"
    >
      <div
        className="tool-call-header"
        onClick={handleToggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleToggle();
          }
        }}
        aria-expanded={expanded}
      >
        <span className="tool-call-chevron">
          {expanded ? '\u25BE' : '\u25B8'}
        </span>
        <span className="tool-call-name">{toolName}</span>
        {!expanded && summary && (
          <span className="tool-call-summary">{summary}</span>
        )}
      </div>
      {expanded && (
        <div className="tool-call-details">
          {paramEntries.map(([key, value]) => (
            <div className="tool-call-param" key={key}>
              <span className="tool-call-param-key">{key}:</span>
              <span className="tool-call-param-value">
                {formatParamValue(value)}
              </span>
            </div>
          ))}
          {result !== null ? (
            <>
              <hr className="tool-call-divider" />
              <div className="tool-call-divider-label">Result</div>
              <div className="tool-call-result">{result}</div>
            </>
          ) : (
            <>
              <hr className="tool-call-divider" />
              <div className="tool-call-running">Running...</div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
