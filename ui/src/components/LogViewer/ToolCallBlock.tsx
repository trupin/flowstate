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

function getInlineSummary(
  toolName: string,
  input: Record<string, unknown>,
): string {
  // Create a compact inline summary like Read(src/main.py), Edit(src/utils.py:42)
  for (const key of PRIMARY_ARG_PRIORITY) {
    if (key in input && typeof input[key] === 'string') {
      const value = input[key] as string;
      if (key === 'file_path') {
        // For file paths, just show the path
        return `${toolName}(${truncateStr(value, 60)})`;
      }
      if (key === 'command') {
        return `${toolName}(${truncateStr(value, 80)})`;
      }
      return `${toolName}(${key}="${truncateStr(value, 60)}")`;
    }
  }
  // Fall back to first string-valued arg
  for (const [key, value] of Object.entries(input)) {
    if (typeof value === 'string') {
      return `${toolName}(${key}="${truncateStr(value, 60)}")`;
    }
  }
  return toolName;
}

function formatParamValue(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  if (value === null || value === undefined) {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
}

function isCodeContent(text: string): boolean {
  // Heuristic: if text contains line numbers or looks like code output
  return (
    text.includes('\n') &&
    (text.match(/^\s*\d+[\t|]/m) !== null || text.length > 300)
  );
}

export function ToolCallBlock(props: ToolCallBlockProps) {
  const { toolName, input, result } = props;
  const [expanded, setExpanded] = useState(false);

  const inlineSummary = useMemo(
    () => getInlineSummary(toolName, input),
    [toolName, input],
  );
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
        <span className="tool-call-badge">{toolName}</span>
        {!expanded && (
          <span className="tool-call-summary">{inlineSummary}</span>
        )}
        {result === null && (
          <span className="tool-call-running-indicator">Running...</span>
        )}
      </div>
      {expanded && (
        <div className="tool-call-details">
          <div className="tool-call-section">
            <div className="tool-call-section-label">Input</div>
            <div className="tool-call-input-content">
              {paramEntries.map(([key, value]) => (
                <div className="tool-call-param" key={key}>
                  <span className="tool-call-param-key">{key}:</span>
                  <span className="tool-call-param-value">
                    {typeof value === 'string' ? (
                      value.includes('\n') ? (
                        <pre>
                          <code>{value}</code>
                        </pre>
                      ) : (
                        formatParamValue(value)
                      )
                    ) : (
                      <pre>
                        <code>{formatParamValue(value)}</code>
                      </pre>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="tool-call-section">
            <div className="tool-call-section-label">Result</div>
            {result !== null ? (
              <div className="tool-call-result">
                {isCodeContent(result) ? (
                  <pre>
                    <code>{result}</code>
                  </pre>
                ) : (
                  result
                )}
              </div>
            ) : (
              <div className="tool-call-running">Running...</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
