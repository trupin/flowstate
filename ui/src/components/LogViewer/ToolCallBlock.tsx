import { useState, useMemo } from 'react';
import './ToolCallBlock.css';

export interface ToolCallBlockProps {
  toolName: string;
  input: Record<string, unknown>;
  result: string | null;
  timestamp: string;
}

// --- Subtask API detection ---

interface SubtaskEvent {
  isCreate: boolean;
  title: string;
  status: string;
}

const SUBTASK_URL_PATTERN = /\/subtasks(?:\/|$)/;
const SUBTASK_METHOD_PATTERN = /(?:^|\s)-X\s+(POST|PATCH)\b/;

function getInputCommand(input: Record<string, unknown>): string | null {
  if (typeof input.command === 'string') return input.command;
  if (typeof input.description === 'string') return input.description;
  return null;
}

function detectSubtaskEvent(
  input: Record<string, unknown>,
  result: string | null,
): SubtaskEvent | null {
  const command = getInputCommand(input);
  if (!command || !SUBTASK_URL_PATTERN.test(command)) return null;
  // Only match POST (create) and PATCH (update), not GET (list)
  const methodMatch = command.match(SUBTASK_METHOD_PATTERN);
  if (!methodMatch) return null;
  const isCreate = methodMatch[1] === 'POST';

  if (!result) return null;

  // The result may contain extra text around the JSON — find the first JSON object
  const jsonStart = result.indexOf('{');
  if (jsonStart === -1) return null;

  try {
    const json: unknown = JSON.parse(result.slice(jsonStart));
    if (json == null || typeof json !== 'object') return null;
    const obj = json as Record<string, unknown>;
    const title = typeof obj.title === 'string' ? obj.title : null;
    const status = typeof obj.status === 'string' ? obj.status : 'unknown';
    if (!title) return null;
    return { isCreate, title, status };
  } catch {
    return null;
  }
}

function subtaskStatusIcon(status: string): string {
  switch (status) {
    case 'done':
      return '\u2705'; // green checkmark
    case 'in_progress':
      return '\u25B6'; // play symbol
    case 'todo':
      return '\u25CB'; // circle
    case 'blocked':
      return '\u26D4'; // no entry
    case 'skipped':
      return '\u23ED'; // skip forward
    default:
      return '\u25CB';
  }
}

// --- End subtask detection ---

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

  const subtaskEvent = useMemo(
    () => detectSubtaskEvent(input, result),
    [input, result],
  );

  const inlineSummary = useMemo(
    () => getInlineSummary(toolName, input),
    [toolName, input],
  );
  const paramEntries = useMemo(() => Object.entries(input), [input]);

  const handleToggle = () => {
    setExpanded((prev) => !prev);
  };

  // Render formatted subtask event instead of the normal tool call block
  if (subtaskEvent) {
    return (
      <div className="tool-call-block" data-testid="tool-call-block">
        <div
          className="log-subtask-event"
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
          <span className="log-subtask-icon">
            {subtaskStatusIcon(subtaskEvent.status)}
          </span>
          <span className="log-subtask-text">
            {subtaskEvent.isCreate ? (
              <>
                Created subtask: <strong>{subtaskEvent.title}</strong>
              </>
            ) : (
              <>
                Subtask <strong>{subtaskEvent.title}</strong>
                {' \u2192 '}
                <span className={`log-subtask-status-${subtaskEvent.status}`}>
                  {subtaskEvent.status}
                </span>
              </>
            )}
          </span>
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
            {result !== null && (
              <div className="tool-call-section">
                <div className="tool-call-section-label">Result</div>
                <div className="tool-call-result">
                  {isCodeContent(result) ? (
                    <pre>
                      <code>{result}</code>
                    </pre>
                  ) : (
                    result
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

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
