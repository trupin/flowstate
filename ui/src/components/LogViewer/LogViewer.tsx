import { useRef, useEffect, useState, useMemo } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { LogEntry } from '../../api/types';
import { ToolCallBlock } from './ToolCallBlock';
import { CollapsibleSection } from './CollapsibleSection';
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

interface ParsedThinking {
  kind: 'thinking';
  text: string;
}

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
  stderr?: string;
}

interface ParsedError {
  kind: 'error';
  message: string;
  stackTrace?: string;
}

interface ParsedSystemInit {
  kind: 'system_init';
}

interface ParsedRateLimitEvent {
  kind: 'rate_limit';
}

interface ParsedActivity {
  kind: 'activity';
  text: string;
}

interface ParsedRaw {
  kind: 'raw';
  text: string;
}

type ParsedContent =
  | ParsedThinking
  | ParsedAssistant
  | ParsedToolUse
  | ParsedToolResult
  | ParsedResult
  | ParsedSystemExit
  | ParsedError
  | ParsedSystemInit
  | ParsedRateLimitEvent
  | ParsedActivity
  | ParsedRaw;

function truncateStr(s: string, maxLen: number): string {
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen - 3) + '...';
}

function extractBlockContent(
  contentArr: unknown,
  blockType: string,
  textField: string,
): string {
  if (!Array.isArray(contentArr)) return '';
  const parts: string[] = [];
  for (const block of contentArr) {
    if (
      block != null &&
      typeof block === 'object' &&
      'type' in block &&
      block.type === blockType &&
      textField in block &&
      typeof (block as Record<string, unknown>)[textField] === 'string'
    ) {
      parts.push((block as Record<string, unknown>)[textField] as string);
    }
  }
  return parts.join('\n');
}

function extractTextFromContent(contentArr: unknown): string {
  return extractBlockContent(contentArr, 'text', 'text');
}

function extractThinkingFromContent(contentArr: unknown): string {
  return extractBlockContent(contentArr, 'thinking', 'thinking');
}

function getFirstMeaningfulLine(text: string): string {
  const lines = text.split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.length > 0) {
      return truncateStr(trimmed, 200);
    }
  }
  return truncateStr(text, 200);
}

function parseLogContent(content: string): ParsedContent {
  // Handle non-JSON process exit messages
  const exitMatch = content.match(/^Process exited with code (\d+)$/);
  if (exitMatch) {
    return {
      kind: 'system_exit',
      exitCode: parseInt(exitMatch[1] ?? '0', 10),
    };
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

  // Executor activity logs: {"subtype": "activity", "message": "..."}
  if (obj.subtype === 'activity' && typeof obj.message === 'string') {
    return { kind: 'activity', text: obj.message };
  }

  if (eventType === 'assistant') {
    const message = obj.message as Record<string, unknown> | undefined;
    if (message) {
      // Check for thinking blocks first
      const thinkingText = extractThinkingFromContent(message.content);
      if (thinkingText) {
        return { kind: 'thinking', text: thinkingText };
      }

      const text = extractTextFromContent(message.content);
      if (text) {
        return { kind: 'assistant', text };
      }

      // Check for stop_reason indicating this is a ResultMessage
      if (typeof message.stop_reason === 'string' && message.stop_reason) {
        const resultText = extractTextFromContent(message.content);
        if (resultText) {
          return { kind: 'result', text: resultText };
        }
      }
    }
    // Streaming partial with no text yet
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
          summary: getFirstMeaningfulLine(text),
        };
      }
    }
    if (typeof obj.content === 'string') {
      return {
        kind: 'tool_result',
        content: obj.content,
        summary: getFirstMeaningfulLine(obj.content),
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

  if (eventType === 'error') {
    const message =
      typeof obj.message === 'string'
        ? obj.message
        : typeof obj.error === 'string'
          ? obj.error
          : 'Unknown error';
    const stackTrace =
      typeof obj.stack === 'string'
        ? obj.stack
        : typeof obj.traceback === 'string'
          ? obj.traceback
          : undefined;
    return { kind: 'error', message, stackTrace };
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
      const stderr = typeof obj.stderr === 'string' ? obj.stderr : undefined;
      return { kind: 'system_exit', exitCode, stderr };
    }
    if (subtype === 'init' || subtype === 'start') {
      return { kind: 'system_init' };
    }
    return { kind: 'raw', text: '' };
  }

  if (eventType === 'rate_limit_event') {
    return { kind: 'rate_limit' };
  }

  if (eventType === 'user') {
    return { kind: 'raw', text: '' };
  }

  // For any other unknown JSON event type, hide rather than show raw JSON
  if (typeof eventType === 'string') {
    return { kind: 'raw', text: '' };
  }

  return { kind: 'raw', text: content };
}

// --- Visibility classification ---

type VisibilityCategory = 'visible' | 'noise' | 'hidden';

function classifyEntry(content: string): VisibilityCategory {
  const parsed = parseLogContent(content);
  // Completely hidden: empty raw text (user, unknown JSON types)
  if (parsed.kind === 'raw' && parsed.text === '') return 'hidden';
  // Noise: system init, rate limit (accessible via "Show all")
  if (parsed.kind === 'system_init') return 'noise';
  if (parsed.kind === 'rate_limit') return 'noise';
  return 'visible';
}

// --- Log entry content rendering ---

const REMARK_PLUGINS = [remarkGfm];

interface ThinkingBlockProps {
  text: string;
  isActive: boolean;
}

function ThinkingBlock({ text, isActive }: ThinkingBlockProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="log-thinking-block">
      <div
        className={`log-thinking-header ${isActive ? '' : 'log-thinking-header-done'}`}
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
        <span className="log-thinking-chevron">
          {expanded ? '\u25BE' : '\u25B8'}
        </span>
        <span className="log-thinking-label">
          {isActive ? 'Thinking' : 'Thoughts'}
        </span>
        {isActive && (
          <span className="log-thinking-dots">
            <span className="dot">.</span>
            <span className="dot">.</span>
            <span className="dot">.</span>
          </span>
        )}
      </div>
      {expanded && <div className="log-thinking-content">{text}</div>}
    </div>
  );
}

interface LogEntryContentProps {
  content: string;
  isLastEntry?: boolean;
}

function LogEntryContent({
  content,
  isLastEntry = false,
}: LogEntryContentProps) {
  const parsed = useMemo(() => parseLogContent(content), [content]);

  switch (parsed.kind) {
    case 'thinking':
      return <ThinkingBlock text={parsed.text} isActive={isLastEntry} />;
    case 'assistant':
      return (
        <div className="log-parsed log-parsed-assistant">
          <Markdown remarkPlugins={REMARK_PLUGINS}>{parsed.text}</Markdown>
        </div>
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
        <div className="log-parsed log-parsed-result">
          <Markdown remarkPlugins={REMARK_PLUGINS}>{parsed.text}</Markdown>
        </div>
      );
    case 'system_exit':
      return (
        <span className="log-parsed log-parsed-exit-container">
          <span
            className={`log-exit-badge ${parsed.exitCode === 0 ? 'exit-success' : 'exit-failure'}`}
          >
            Exit {parsed.exitCode}
          </span>
          {parsed.stderr && (
            <CollapsibleSection label="stderr">
              <pre className="log-stderr-content">{parsed.stderr}</pre>
            </CollapsibleSection>
          )}
        </span>
      );
    case 'error':
      return (
        <div className="log-parsed log-parsed-error">
          <span className="log-error-message">{parsed.message}</span>
          {parsed.stackTrace && (
            <CollapsibleSection label="Stack trace">
              <pre className="log-stacktrace">{parsed.stackTrace}</pre>
            </CollapsibleSection>
          )}
        </div>
      );
    case 'system_init':
      return (
        <span className="log-parsed log-parsed-system">Session started</span>
      );
    case 'rate_limit':
      return <span className="log-parsed log-parsed-system">Rate limited</span>;
    case 'activity':
      return <span className="log-parsed log-activity">{parsed.text}</span>;
    case 'raw':
      return <span className="log-content">{parsed.text}</span>;
  }
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
      // No matching result yet -- tool is still running
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

    // Skip standalone tool_results that were already consumed in a group
    if (parsed.kind === 'tool_result') {
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
  const [showAll, setShowAll] = useState(false);

  const { filteredLogs, noiseCount } = useMemo(() => {
    const filtered: LogEntry[] = [];
    let noise = 0;
    for (const entry of logs) {
      const category = classifyEntry(entry.content);
      if (category === 'noise') {
        noise++;
        if (showAll) filtered.push(entry);
      } else if (category === 'visible') {
        filtered.push(entry);
      }
      // 'hidden' entries are always excluded
    }
    return { filteredLogs: filtered, noiseCount: noise };
  }, [logs, showAll]);

  const groupedEntries = useMemo(
    () => groupLogEntries(filteredLogs),
    [filteredLogs],
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
    setShowAll(false);
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
          {noiseCount > 0 && (
            <button
              className={`log-viewer-show-all ${showAll ? 'active' : ''}`}
              onClick={() => setShowAll((prev) => !prev)}
              title={
                showAll
                  ? 'Hide system noise'
                  : `Show all (${noiseCount} hidden)`
              }
            >
              {showAll ? 'Hide noise' : `Show all (${noiseCount})`}
            </button>
          )}
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
          groupedEntries.map((grouped, index) => {
            const isLast = index === groupedEntries.length - 1;
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
                <LogEntryContent
                  content={grouped.entry.content}
                  isLastEntry={isLast}
                />
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
