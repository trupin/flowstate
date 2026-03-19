# [UI-006] Log Viewer (raw streaming)

## Domain
ui

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-002
- Blocks: UI-011

## Spec References
- specs.md Section 10.5 — "Log Viewer"
- agents/05-ui.md — "Log Viewer (`LogViewer.tsx`)"

## Summary
Create the log viewer component that displays raw streaming output for a selected task. The log viewer occupies ~40% of the Run Detail page and shows real-time output from Claude Code subprocess execution. It receives `task.log` WebSocket events and renders them line-by-line in a monospace font on a dark background. Auto-scroll to bottom is enabled by default with a pin/unpin toggle, and a client-side "Clear" button is provided.

## Acceptance Criteria
- [ ] Log viewer container includes `data-testid="log-viewer"` attribute (required for E2E tests)
- [ ] `ui/src/components/LogViewer.tsx` exists and renders a scrollable log panel
- [ ] `ui/src/components/LogViewer.css` exists with log viewer styles
- [ ] Displays raw text output line-by-line in a monospace font
- [ ] No structured parsing of tool_use/tool_result — everything is raw text
- [ ] Dark background consistent with the theme (`var(--bg-primary)` or similar)
- [ ] Auto-scrolls to the bottom as new lines arrive
- [ ] Pin/unpin toggle button: when pinned (default), auto-scrolls; when unpinned, stays at current scroll position
- [ ] If user manually scrolls up, auto-pin is disabled; clicking the pin button re-enables it
- [ ] "Clear" button clears the displayed logs client-side (does not affect server)
- [ ] When no task is selected, shows a placeholder message ("Select a node to view logs")
- [ ] When a task is selected but has no logs, shows "No output yet"
- [ ] Log lines include timestamp (optional, from event data)
- [ ] Handles large volumes of output without freezing (thousands of lines)

## Technical Design

### Files to Create/Modify
- `ui/src/components/LogViewer.tsx` — log viewer component
- `ui/src/components/LogViewer.css` — log viewer styles

### Key Implementation Details

#### Props interface

```typescript
interface LogViewerProps {
    logs: LogEntry[];           // log lines for the selected task
    taskName?: string | null;   // name of the selected task (for header display)
    onClear?: () => void;       // callback when user clicks Clear
}

interface LogEntry {
    id: number;
    content: string;
    log_type: string;           // stdout, stderr, tool_use, assistant_message, system
    timestamp: string;
}
```

#### Component structure

```typescript
import { useRef, useEffect, useState } from 'react';
import './LogViewer.css';

export function LogViewer({ logs, taskName, onClear }: LogViewerProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const [pinned, setPinned] = useState(true);

    // Auto-scroll when pinned and new logs arrive
    useEffect(() => {
        if (pinned && containerRef.current) {
            containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
    }, [logs, pinned]);

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
            <div className="log-viewer log-viewer-empty">
                <span>Select a node to view logs</span>
            </div>
        );
    }

    return (
        <div className="log-viewer">
            <div className="log-viewer-header">
                <span className="log-viewer-title">{taskName}</span>
                <div className="log-viewer-controls">
                    <button
                        className={`log-viewer-pin ${pinned ? 'active' : ''}`}
                        onClick={() => {
                            setPinned(!pinned);
                            if (!pinned && containerRef.current) {
                                containerRef.current.scrollTop = containerRef.current.scrollHeight;
                            }
                        }}
                        title={pinned ? 'Auto-scroll ON' : 'Auto-scroll OFF'}
                    >
                        {pinned ? '⬇ Pinned' : '⬇ Unpinned'}
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
                    logs.map(entry => (
                        <div key={entry.id} className={`log-line log-type-${entry.log_type}`}>
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
```

#### CSS

```css
.log-viewer {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg-primary);
    border-left: 1px solid var(--border);
}

.log-viewer-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-secondary);
    font-size: 13px;
}

.log-viewer-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    min-height: 36px;
}

.log-viewer-title {
    font-weight: 600;
    font-size: 13px;
}

.log-viewer-controls {
    display: flex;
    gap: 6px;
}

.log-viewer-controls button {
    font-size: 11px;
    padding: 3px 8px;
}

.log-viewer-pin.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
}

.log-viewer-content {
    flex: 1;
    overflow-y: auto;
    padding: 8px 12px;
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
}

.log-line {
    display: flex;
    gap: 8px;
}

.log-timestamp {
    color: var(--text-secondary);
    flex-shrink: 0;
    font-size: 11px;
    user-select: none;
}

.log-content {
    color: var(--text-primary);
}

/* Subtle color hint for stderr */
.log-type-stderr .log-content {
    color: var(--error);
}

.log-viewer-no-output {
    color: var(--text-secondary);
    padding: 20px 0;
    text-align: center;
    font-family: var(--font-sans);
    font-size: 13px;
}
```

#### Timestamp formatting

```typescript
function formatTimestamp(iso: string): string {
    const d = new Date(iso);
    return d.toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}
```

Only show HH:MM:SS — no date, no milliseconds. Keeps the timestamp column narrow.

#### Performance considerations

For very long logs (thousands of lines), rendering every line as a DOM element is acceptable for MVP (modern browsers handle 5000+ divs fine). If performance becomes an issue in Phase 3, consider:
- Virtualized scrolling (e.g., `react-window`) — not needed for MVP
- Truncating old lines (keep last N lines in state)

For now, keep it simple: render all lines, rely on the browser's own scroll performance.

### Edge Cases
- Empty logs array with a selected task — show "No output yet"
- No task selected — show "Select a node to view logs"
- Very long single line (no newlines) — `word-break: break-word` ensures it wraps
- Rapid log updates (many events per second) — React batches state updates; ensure `logs` is passed as a stable array reference when unchanged to avoid unnecessary re-renders
- User clears logs, then new logs arrive — the new logs should appear (Clear only resets the display, parent state continues accumulating)
- Switching selected task — parent resets the `logs` prop to the new task's log array; pin resets to true
- Unicode / ANSI escape codes in log output — render as-is for MVP (ANSI stripping can be added later)

## Testing Strategy
Minimal for MVP:
1. Component renders without crashing with empty logs
2. Component renders without crashing with no task selected
3. Component renders log lines when given data
4. Visual verification: monospace font, dark background, auto-scroll works
