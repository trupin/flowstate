import { useRef, useEffect, useState, useMemo } from 'react';
import type { LogEntry } from '../../api/types';
import { ToolCallBlock } from './ToolCallBlock';
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

// --- Parsed log entry types ---

interface ParsedAssistant {
  kind: 'assistant';
  text: string;
}

interface ParsedToolUse {
  kind: 'tool_use';
  toolName: string;
  toolId: string;
  input: Record<string, unknown>;
  inputSummary: string;
}

interface ParsedToolResult {
  kind: 'tool_result';
  content: string;
  summary: string;
}

interface ParsedResult {
  kind: 'result';
  text: string;
}

interface ParsedSystemExit {
  kind: 'system_exit';
  exitCode: number;
}

interface ParsedSystemInit {
  kind: 'system_init';
}

interface ParsedRaw {
  kind: 'raw';
  text: string;
}

type ParsedContent =
  | ParsedAssistant
  | ParsedToolUse
  | ParsedToolResult
  | ParsedResult
  | ParsedSystemExit
  | ParsedSystemInit
  | ParsedRaw;

function truncateStr(s: string, maxLen: number): string {
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen - 3) + '...';
}

function extractTextFromContent(contentArr: unknown): string {
  if (!Array.isArray(contentArr)) return '';
  const parts: string[] = [];
  for (const block of contentArr) {
    if (
      block != null &&
      typeof block === 'object' &&
      'type' in block &&
      block.type === 'text' &&
      'text' in block &&
      typeof block.text === 'string'
    ) {
      parts.push(block.text);
    }
  }
  return parts.join('\n');
}

function parseLogContent(content: string): ParsedContent {
  // Handle non-JSON process exit messages
  const exitMatch = content.match(/^Process exited with code (\d+)$/);
  if (exitMatch) {
    return { kind: 'system_exit', exitCode: parseInt(exitMatch[1] ?? '0', 10) };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return { kind: 'raw', text: content };
  }

  if (parsed == null || typeof parsed !== 'object') {
    return { kind: 'raw', text: content };
  }

  const obj = parsed as Record<string, unknown>;
  const eventType = obj.type;

  if (eventType === 'assistant') {
    const message = obj.message as Record<string, unknown> | undefined;
    if (message) {
      const text = extractTextFromContent(message.content);
      if (text) {
        return { kind: 'assistant', text };
      }
    }
    // Streaming partial with no text yet — hide it
    return { kind: 'raw', text: '' };
  }

  if (eventType === 'tool_use') {
    const message = obj.message as Record<string, unknown> | undefined;
    if (message && Array.isArray(message.content)) {
      for (const block of message.content) {
        if (
          block != null &&
          typeof block === 'object' &&
          'type' in block &&
          (block as Record<string, unknown>).type === 'tool_use'
        ) {
          const b = block as Record<string, unknown>;
          const toolName = typeof b.name === 'string' ? b.name : 'unknown tool';
          const toolId = typeof b.id === 'string' ? b.id : '';
          const rawInput = b.input;
          const input: Record<string, unknown> =
            rawInput != null && typeof rawInput === 'object'
              ? (rawInput as Record<string, unknown>)
              : {};
          const keys = Object.keys(input);
          let inputSummary = keys.slice(0, 3).join(', ');
          if (keys.length > 3) inputSummary += ', ...';
          return { kind: 'tool_use', toolName, toolId, input, inputSummary };
        }
      }
    }
    // Tool use event with no parseable tool — hide it
    return { kind: 'raw', text: '' };
  }

  if (eventType === 'tool_result') {
    const message = obj.message as Record<string, unknown> | undefined;
    if (message && Array.isArray(message.content)) {
      const text = extractTextFromContent(message.content);
      if (text) {
        return {
          kind: 'tool_result',
          content: text,
          summary: truncateStr(text, 200),
        };
      }
    }
    // Also handle direct content field
    if (typeof obj.content === 'string') {
      return {
        kind: 'tool_result',
        content: obj.content,
        summary: truncateStr(obj.content, 200),
      };
    }
    return {
      kind: 'tool_result',
      content: 'Tool completed',
      summary: 'Tool completed',
    };
  }

  if (eventType === 'result') {
    const resultText =
      typeof obj.result === 'string'
        ? obj.result
        : typeof obj.text === 'string'
          ? obj.text
          : JSON.stringify(obj.result ?? obj);
    return { kind: 'result', text: resultText };
  }

  if (eventType === 'system') {
    const subtype = obj.subtype ?? obj.event;
    if (subtype === 'process_exit' || subtype === 'exit') {
      const exitCode =
        typeof obj.exit_code === 'number'
          ? obj.exit_code
          : typeof (obj.payload as Record<string, unknown> | undefined)
                ?.exit_code === 'number'
            ? ((obj.payload as Record<string, unknown>).exit_code as number)
            : 0;
      return { kind: 'system_exit', exitCode };
    }
    // Hide all other system subtypes (init, start, task_progress, etc.)
    return { kind: 'raw', text: '' };
  }

  if (eventType === 'rate_limit_event') {
    // Hide rate limit noise — return null-like marker
    return { kind: 'raw', text: '' };
  }

  if (eventType === 'user') {
    // User events are tool results sent back to the model — hide by default
    return { kind: 'raw', text: '' };
  }

  // For any other unknown JSON event type, hide rather than show raw JSON
  if (typeof eventType === 'string') {
    return { kind: 'raw', text: '' };
  }

  return { kind: 'raw', text: content };
}

function LogEntryContent({ content }: { content: string }) {
  const parsed = useMemo(() => parseLogContent(content), [content]);

  switch (parsed.kind) {
    case 'assistant':
      return (
        <span className="log-parsed log-parsed-assistant">{parsed.text}</span>
      );
    case 'tool_use':
      return (
        <span className="log-parsed log-parsed-tool-use">
          <span className="log-tool-name">{parsed.toolName}</span>
          {parsed.inputSummary && (
            <span className="log-tool-args">({parsed.inputSummary})</span>
          )}
        </span>
      );
    case 'tool_result':
      return (
        <span className="log-parsed log-parsed-tool-result">
          {parsed.summary}
        </span>
      );
    case 'result':
      return (
        <span className="log-parsed log-parsed-result">{parsed.text}</span>
      );
    case 'system_exit':
      return (
        <span
          className={`log-parsed log-parsed-exit ${parsed.exitCode === 0 ? 'exit-success' : 'exit-failure'}`}
        >
          Process exited with code {parsed.exitCode}
        </span>
      );
    case 'system_init':
      return (
        <span className="log-parsed log-parsed-system">Session started</span>
      );
    case 'raw':
      return <span className="log-content">{parsed.text}</span>;
  }
}

function shouldHideEntry(content: string): boolean {
  const parsed = parseLogContent(content);
  // Hide entries that parsed to empty raw text (rate_limit, user, unknown JSON types)
  if (parsed.kind === 'raw' && parsed.text === '') return true;
  // Hide system init (noise)
  if (parsed.kind === 'system_init') return true;
  return false;
}

// --- Grouped log entry types ---

interface GroupedToolCall {
  type: 'tool_call';
  toolUse: ParsedToolUse;
  toolResult: ParsedToolResult | null;
  timestamp: string;
  ids: number[];
}

interface GroupedSingle {
  type: 'single';
  entry: LogEntry;
}

type GroupedEntry = GroupedToolCall | GroupedSingle;

function groupLogEntries(logs: LogEntry[]): GroupedEntry[] {
  const result: GroupedEntry[] = [];
  let i = 0;

  while (i < logs.length) {
    const entry = logs[i];
    if (!entry) {
      i++;
      continue;
    }

    const parsed = parseLogContent(entry.content);

    if (parsed.kind === 'tool_use') {
      // Look at the next non-hidden entry for a matching tool_result
      const nextEntry = logs[i + 1];
      if (nextEntry) {
        const nextParsed = parseLogContent(nextEntry.content);
        if (nextParsed.kind === 'tool_result') {
          result.push({
            type: 'tool_call',
            toolUse: parsed,
            toolResult: nextParsed,
            timestamp: entry.timestamp,
            ids: [entry.id, nextEntry.id],
          });
          i += 2;
          continue;
        }
      }
      // No matching result yet — tool is still running
      result.push({
        type: 'tool_call',
        toolUse: parsed,
        toolResult: null,
        timestamp: entry.timestamp,
        ids: [entry.id],
      });
      i++;
      continue;
    }

    // Skip standalone tool_results that were already consumed in a group.
    // This handles the case where the render loop already grouped them above.
    // However, standalone tool_results (not preceded by a tool_use) still render.
    if (parsed.kind === 'tool_result') {
      // Check if this was already consumed by the previous group
      if (result.length > 0) {
        const lastGroup = result[result.length - 1];
        if (
          lastGroup &&
          lastGroup.type === 'tool_call' &&
          lastGroup.ids.includes(entry.id)
        ) {
          i++;
          continue;
        }
      }
    }

    result.push({ type: 'single', entry });
    i++;
  }

  return result;
}

export function LogViewer({ logs, taskName, onClear }: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  const visibleLogs = useMemo(
    () => logs.filter((entry) => !shouldHideEntry(entry.content)),
    [logs],
  );

  const groupedEntries = useMemo(
    () => groupLogEntries(visibleLogs),
    [visibleLogs],
  );

  // Auto-scroll when pinned and new logs arrive
  useEffect(() => {
    if (pinned && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [groupedEntries, pinned]);

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
        {groupedEntries.length === 0 ? (
          <div className="log-viewer-no-output">No output yet</div>
        ) : (
          groupedEntries.map((grouped) => {
            if (grouped.type === 'tool_call') {
              const key = grouped.ids.join('-');
              return (
                <div key={key} className="log-line log-type-tool_use">
                  <span className="log-timestamp">
                    {formatTimestamp(grouped.timestamp)}
                  </span>
                  <ToolCallBlock
                    toolName={grouped.toolUse.toolName}
                    input={grouped.toolUse.input}
                    result={
                      grouped.toolResult !== null
                        ? grouped.toolResult.content
                        : null
                    }
                    timestamp={grouped.timestamp}
                  />
                </div>
              );
            }
            return (
              <div
                key={grouped.entry.id}
                className={`log-line log-type-${grouped.entry.log_type}`}
              >
                <span className="log-timestamp">
                  {formatTimestamp(grouped.entry.timestamp)}
                </span>
                <LogEntryContent content={grouped.entry.content} />
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
