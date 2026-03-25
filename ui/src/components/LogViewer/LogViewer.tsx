import { useRef, useEffect, useState, useMemo, useCallback } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { LogEntry, NodeType, TaskStatus } from '../../api/types';
import { ApiError, api } from '../../api/client';
import { ClickablePath } from '../ClickablePath';
import { ToolCallBlock } from './ToolCallBlock';
import { CollapsibleSection } from './CollapsibleSection';
import { SubtaskProgress } from './SubtaskProgress';
import { useSubtasks } from '../../hooks/useSubtasks';
import './LogViewer.css';

// --- Task execution metadata for the details panel ---

export interface TaskExecutionInfo {
  nodeType: NodeType;
  elapsedSeconds: number | null;
  cwd: string | null;
  taskDir: string | null;
  worktreeDir: string | null;
  status: TaskStatus;
  waitUntil: string | null;
}

export interface LogViewerProps {
  logs: LogEntry[];
  taskName?: string | null;
  taskExecution?: TaskExecutionInfo | null;
  isAutoFollow?: boolean;
  showFollowButton?: boolean;
  onFollowClick?: () => void;
  onClear?: () => void;
  runId?: string;
  taskExecutionId?: string;
  subtaskVersion?: number;
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

// --- Helpers for the details panel ---

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

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

// --- Node details panel ---

interface NodeDetailsPanelProps {
  execution: TaskExecutionInfo;
}

function NodeDetailsPanel({ execution }: NodeDetailsPanelProps) {
  const hasExecution =
    execution.status !== 'pending' ||
    execution.cwd != null ||
    execution.taskDir != null;

  return (
    <div className="log-viewer-details">
      <span className="log-viewer-details-type-badge">
        {execution.nodeType}
      </span>
      {execution.elapsedSeconds != null && (
        <span className="log-viewer-details-elapsed">
          {formatElapsed(execution.elapsedSeconds)}
        </span>
      )}
      {hasExecution ? (
        <div className="log-viewer-details-dirs">
          {execution.cwd && (
            <span className="log-viewer-details-dir">
              <span className="log-viewer-details-dir-label">cwd</span>
              <ClickablePath path={execution.cwd} truncate={50} />
            </span>
          )}
          {execution.taskDir && (
            <span className="log-viewer-details-dir">
              <span className="log-viewer-details-dir-label">task</span>
              <ClickablePath path={execution.taskDir} truncate={50} />
            </span>
          )}
          {execution.worktreeDir && (
            <span className="log-viewer-details-dir">
              <span className="log-viewer-details-dir-label">worktree</span>
              <ClickablePath path={execution.worktreeDir} truncate={50} />
            </span>
          )}
        </div>
      ) : (
        <span className="log-viewer-details-not-executed">
          Not yet executed
        </span>
      )}
      {execution.status === 'waiting' && execution.waitUntil && (
        <span className="log-viewer-details-countdown">
          Wait: <CountdownTimer until={execution.waitUntil} />
        </span>
      )}
    </div>
  );
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

interface ParsedUserInput {
  kind: 'user_input';
  message: string;
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
  | ParsedRaw
  | ParsedUserInput;

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

function parseLogContent(
  content: string,
  logType?: LogEntry['log_type'],
): ParsedContent {
  // Handle user_input log type BEFORE JSON parsing — this must not be
  // caught by the generic eventType === 'user' filter below.
  if (logType === 'user_input') {
    return { kind: 'user_input', message: content };
  }

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

function classifyEntry(
  content: string,
  logType?: LogEntry['log_type'],
): VisibilityCategory {
  const parsed = parseLogContent(content, logType);
  // User input is always visible
  if (parsed.kind === 'user_input') return 'visible';
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
  logType?: LogEntry['log_type'];
  isLastEntry?: boolean;
}

function LogEntryContent({
  content,
  logType,
  isLastEntry = false,
}: LogEntryContentProps) {
  const parsed = useMemo(
    () => parseLogContent(content, logType),
    [content, logType],
  );

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
    case 'user_input':
      return (
        <div className="log-entry-user-input">
          <span className="log-entry-user-label">You</span>
          <span className="log-entry-user-message">{parsed.message}</span>
        </div>
      );
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

    const parsed = parseLogContent(entry.content, entry.log_type);

    if (parsed.kind === 'tool_use') {
      // Look at the next non-hidden entry for a matching tool_result
      const nextEntry = logs[i + 1];
      if (nextEntry) {
        const nextParsed = parseLogContent(
          nextEntry.content,
          nextEntry.log_type,
        );
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

export function LogViewer({
  logs,
  taskName,
  taskExecution,
  isAutoFollow = false,
  showFollowButton = false,
  onFollowClick,
  onClear,
  runId,
  taskExecutionId,
  subtaskVersion = 0,
}: LogViewerProps) {
  const { subtasks, loading: subtasksLoading } = useSubtasks(
    runId,
    taskExecutionId,
    subtaskVersion,
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [pinned, setPinned] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [sending, setSending] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const [inputError, setInputError] = useState<string | null>(null);

  const { filteredLogs, noiseCount } = useMemo(() => {
    const filtered: LogEntry[] = [];
    let noise = 0;
    for (const entry of logs) {
      const category = classifyEntry(entry.content, entry.log_type);
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

  // Reset pin state and details when task changes via manual selection.
  // Auto-follow transitions should not reset the user's scroll/filter state.
  useEffect(() => {
    if (!isAutoFollow) {
      setPinned(true);
      setShowAll(false);
      setShowDetails(false);
    }
  }, [taskName, isAutoFollow]);

  // Detect manual scroll-up to auto-unpin
  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 30;
    if (!isAtBottom && pinned) {
      setPinned(false);
    }
  };

  // Clear input state when selected task changes
  useEffect(() => {
    setInputValue('');
    setSending(false);
    setInterrupting(false);
    setInputError(null);
  }, [taskExecutionId]);

  const handleSend = useCallback(async () => {
    if (!runId || !taskExecutionId || !inputValue.trim() || sending) return;
    setSending(true);
    setInputError(null);
    try {
      await api.taskInteraction.sendMessage(
        runId,
        taskExecutionId,
        inputValue.trim(),
      );
      setInputValue('');
      inputRef.current?.focus();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setInputError('Task is no longer running');
      } else {
        setInputError('Failed to send message');
      }
    } finally {
      setSending(false);
    }
  }, [runId, taskExecutionId, inputValue, sending]);

  const handleInterrupt = useCallback(async () => {
    if (!runId || !taskExecutionId || interrupting) return;
    setInterrupting(true);
    setInputError(null);
    try {
      await api.taskInteraction.interrupt(runId, taskExecutionId);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setInputError('Task is no longer running');
      } else {
        setInputError('Failed to interrupt task');
      }
      setInterrupting(false);
    }
    // Don't reset interrupting here — it will be cleared when status changes
  }, [runId, taskExecutionId, interrupting]);

  // Reset interrupting state when task status changes away from running
  useEffect(() => {
    if (taskExecution?.status !== 'running') {
      setInterrupting(false);
    }
  }, [taskExecution?.status]);

  const showInputBar =
    taskExecution?.status === 'running' ||
    taskExecution?.status === 'interrupted';

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
          {showFollowButton && onFollowClick && (
            <button
              className="log-viewer-follow-btn"
              onClick={onFollowClick}
              title="Resume auto-follow to track the running task"
            >
              Follow
            </button>
          )}
          {taskExecution && (
            <button
              className={`log-viewer-details-btn ${showDetails ? 'active' : ''}`}
              onClick={() => setShowDetails((prev) => !prev)}
              title={showDetails ? 'Hide node details' : 'Show node details'}
            >
              Details
            </button>
          )}
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
      {showDetails && taskExecution && (
        <NodeDetailsPanel execution={taskExecution} />
      )}
      {showDetails && taskExecutionId && (
        <SubtaskProgress subtasks={subtasks} loading={subtasksLoading} />
      )}
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
                  logType={grouped.entry.log_type}
                  isLastEntry={isLast}
                />
              </div>
            );
          })
        )}
      </div>
      {showInputBar && (
        <div className="log-viewer-input-bar">
          {taskExecution.status === 'running' && (
            <button
              className="log-viewer-interrupt-btn"
              onClick={handleInterrupt}
              disabled={interrupting}
              title="Stop the agent to send a message"
            >
              {interrupting ? 'Interrupting...' : 'Interrupt'}
            </button>
          )}
          <input
            ref={inputRef}
            type="text"
            className="log-viewer-input"
            value={inputValue}
            onChange={(e) => {
              setInputValue(e.target.value);
              setInputError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !sending) {
                void handleSend();
              }
            }}
            placeholder={
              taskExecution.status === 'interrupted'
                ? 'Send a message to resume the agent...'
                : 'Send a message to the agent...'
            }
            disabled={sending}
          />
          <button
            className="log-viewer-send-btn"
            onClick={() => void handleSend()}
            disabled={sending || !inputValue.trim()}
          >
            {sending ? 'Sending...' : 'Send'}
          </button>
        </div>
      )}
      {inputError && <div className="log-viewer-input-error">{inputError}</div>}
    </div>
  );
}
