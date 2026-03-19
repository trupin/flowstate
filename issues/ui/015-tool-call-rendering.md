# [UI-015] Rich Tool Call Rendering in Log Viewer

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: UI-006 (Log Viewer)
- Blocks: —

## Spec References
- specs.md Section 9.2 — "Output Capture" (tool_use, tool_result event types)
- specs.md Section 10 — "Web Interface"

## Summary
The log viewer currently renders tool calls as a single flat line showing only the tool name and parameter key names (e.g., `Read (file_path, ...)`), with tool results truncated to 200 characters on a separate dimmed line. This makes it hard to understand what an agent is doing. The goal is to render tool calls similarly to how Claude Code displays them in the terminal: a collapsible block showing the tool name as a header, the full input parameters formatted readably, and the tool result below — all visually grouped together.

## Current Behavior
```
14:23:01  Read (file_path)
14:23:01  /Users/foo/src/main.py (first 200 chars of file content...)
14:23:02  Edit (file_path, old_string, new_string)
14:23:02  The file has been updated successfully.
```

- Tool use: shows tool name + arg key names only (no values)
- Tool result: shows first 200 chars as a separate dimmed line
- No visual grouping between a tool_use and its tool_result
- No way to expand to see full input/output

## Desired Behavior
```
14:23:01  ▸ Read                                            ← clickable, collapsed by default
            file_path: /Users/foo/src/main.py               ← shown when expanded
            ─── Result ───
            (full file contents, scrollable)

14:23:02  ▸ Edit                                            ← collapsed
            file_path: /Users/foo/src/main.py
            old_string: "function foo() {"
            new_string: "function bar() {"
            ─── Result ───
            The file has been updated successfully.
```

Key features:
1. **Collapsible tool blocks**: tool_use + tool_result grouped into one visual unit
2. **Tool name as header**: bold, accent-colored, with expand/collapse chevron (▸/▾)
3. **Full input parameters**: show all parameter key-value pairs when expanded, with values syntax-highlighted or at least properly formatted
4. **Tool result inline**: shown below the inputs within the same block, separated by a subtle divider
5. **Collapsed by default**: shows just the tool name + a one-line summary (similar to Claude Code's compact view)
6. **Collapsed summary**: when collapsed, show tool name + first meaningful arg value truncated (e.g., `Read file_path="/Users/foo/src/main.py"` or `Bash command="git status"`)
7. **Long values**: truncate in collapsed view, show full in expanded view with overflow scroll

## Acceptance Criteria
- [ ] Tool use events render as collapsible blocks with a clickable header
- [ ] Collapsed view shows tool name + primary argument value (first string arg, truncated to ~80 chars)
- [ ] Expanded view shows all input parameters as key-value pairs
- [ ] Tool result is grouped with its corresponding tool_use (matched by position in the log stream)
- [ ] Expanded view shows the full tool result below the inputs, separated by a divider
- [ ] Long tool results (e.g., file contents) are scrollable within a max-height container
- [ ] Collapse/expand state persists during scrolling (no re-renders resetting state)
- [ ] Code/file content in tool results uses monospace font with basic syntax awareness
- [ ] Visual style matches the dark theme (accent color for tool name, secondary for args, border for grouping)
- [ ] Non-tool log entries (assistant, result, system) are unaffected

## Technical Design

### Files to Create/Modify
- `ui/src/components/LogViewer/ToolCallBlock.tsx` — New component for collapsible tool call rendering
- `ui/src/components/LogViewer/ToolCallBlock.css` — Styles for tool call blocks
- `ui/src/components/LogViewer/LogViewer.tsx` — Integrate ToolCallBlock, group tool_use + tool_result pairs
- `ui/src/components/LogViewer/LogViewer.css` — Minor adjustments for block layout

### Key Implementation Details

#### Grouping tool_use + tool_result

The log stream interleaves events. A tool_use is always followed (eventually) by a corresponding tool_result for the same tool call. Group them by scanning the visible logs:

```typescript
interface ToolCallGroup {
  toolUse: { toolName: string; input: Record<string, unknown> };
  toolResult: { content: string } | null;  // null if still pending
  timestamp: string;
  logIds: number[];  // IDs of the log entries in this group
}
```

Strategy: iterate through logs, when a `tool_use` is found, look ahead for the next `tool_result` to pair them. If no result yet (tool still running), show the tool call block without a result section.

#### ToolCallBlock component

```typescript
interface ToolCallBlockProps {
  toolName: string;
  input: Record<string, unknown>;
  result: string | null;
  timestamp: string;
  defaultExpanded?: boolean;
}
```

- Renders a bordered container with the tool name header
- Chevron (▸/▾) toggles expanded state
- Collapsed: `▸ Read  file_path="/Users/foo/src/main.py"`
- Expanded: full key-value pairs + result

#### Collapsed summary logic

Pick the "primary" argument to show in the collapsed summary:
1. If tool has `file_path` → show that
2. If tool has `command` → show that
3. If tool has `pattern` → show that
4. Otherwise → show first string-valued arg
5. Truncate to ~80 chars

#### Styling

- Tool block: `border-left: 2px solid var(--accent)`, slight left padding, subtle background
- Tool name: bold, accent color, cursor pointer
- Args: `color: var(--text-secondary)`, key in normal weight, value in primary color
- Result divider: thin border or `───` separator
- Result content: max-height with overflow-y scroll for long outputs
- Collapsed: single line, no border-left highlight (or lighter)

### Edge Cases
- Tool use with no matching result (still running) — show without result section, add a "running..." indicator
- Tool result arrives later (WebSocket stream) — update the block when result log entry appears
- Multiple tool calls in rapid succession — each gets its own block
- Tool with empty input — show tool name only, no args section
- Tool result that is very long (>10K chars) — cap at max-height with scroll, don't collapse by default if result just arrived
- Non-JSON tool result content — render as plain text

## Testing Strategy
1. Component renders collapsed by default with tool name and primary arg
2. Clicking header toggles expanded state
3. Expanded view shows all input parameters
4. Tool result is displayed when available
5. Long results are scrollable (max-height applied)
6. Missing tool result shows "running..." indicator
7. Non-tool log entries are unaffected by the grouping logic
