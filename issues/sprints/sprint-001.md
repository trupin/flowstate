# Sprint 001

**Issues**: ENGINE-057, UI-060
**Domains**: engine, ui
**Date**: 2026-03-27

## Acceptance Tests

### ENGINE-057: Filter noise streaming chunks at ACP bridge before storage

TEST-1: Empty text chunks are not stored
  Given: An ACP agent session producing streaming output that includes empty string chunks
  When: The agent completes a prompt and all streaming events are stored in task_logs
  Then: No task_log entries exist with log_type "assistant" and content containing only an empty string or whitespace

TEST-2: Single non-alphanumeric character chunks are not stored
  Given: An ACP agent session producing streaming output that includes single-character fragments like ".", ",", ":", ";", "!"
  When: The agent completes a prompt and all streaming events are stored in task_logs
  Then: No task_log entries exist with log_type "assistant" where the text content is a single non-alphanumeric character

TEST-3: Single alphanumeric characters pass through
  Given: An ACP agent session producing a streaming chunk containing a single letter "A" or digit "7"
  When: The chunk is processed by the ACP bridge
  Then: The chunk is stored as a task_log entry (single letters/digits may be the start of a streamed word)

TEST-4: Multi-character content passes through unchanged
  Given: An ACP agent session producing chunks like "Hello world", "def foo():", "..."
  When: The chunks are processed by the ACP bridge
  Then: All such chunks are stored as task_log entries with their full content preserved

TEST-5: Tool call events are not affected by the filter
  Given: An ACP agent session that invokes a tool
  When: ToolCallStart and ToolCallProgress updates are received
  Then: All tool_use StreamEvents are emitted regardless of their content (including single-char tool names or inputs)

TEST-6: Tool result events are not affected by the filter
  Given: An ACP agent session receiving a tool result
  When: The tool result update is received (even if the result text is a single character or empty)
  Then: The tool_result StreamEvent is emitted unchanged

TEST-7: System/plan events are not affected by the filter
  Given: An ACP agent session emitting an AgentPlanUpdate
  When: The plan update is processed
  Then: The system StreamEvent is emitted regardless of plan entry content

TEST-8: Thinking chunks are filtered the same as assistant chunks
  Given: An ACP agent session producing AgentThoughtChunk updates with empty or single-punctuation content
  When: The chunks are processed by the ACP bridge
  Then: Empty/whitespace-only and single-non-alphanumeric thinking chunks are not emitted as StreamEvents

TEST-9: Existing tests pass with no regressions
  Given: The noise filter is implemented in acp_client.py
  When: Running the full pytest suite
  Then: All existing tests pass

### UI-060: Improve log noise classification for single-char and tool result fragments

TEST-10: Single punctuation characters classified as noise
  Given: Existing task_log entries in the database with content containing a single ".", ",", ":", ";", "!", or "?" as assistant text
  When: The log viewer renders the task's logs with default filter settings (noise hidden)
  Then: Those single-punctuation entries are not displayed in the log viewer

TEST-11: Single punctuation entries shown with "Show all"
  Given: Existing task_log entries with single punctuation assistant content
  When: The user clicks the "Show all" button in the log viewer
  Then: Those entries become visible in the log list

TEST-12: "Tool completed" tool_result entries classified as noise
  Given: Existing task_log entries with log_type "tool_result" and content that parses to exactly "Tool completed"
  When: The log viewer renders with default filter settings
  Then: Those "Tool completed" entries are not displayed (classified as noise, accessible via "Show all")

TEST-13: Tool results with real content remain visible
  Given: Existing task_log entries with log_type "tool_result" and content containing actual output text (e.g., file contents, command output)
  When: The log viewer renders with default filter settings
  Then: Those tool result entries are displayed normally as visible entries

TEST-14: Multi-character assistant text is unaffected
  Given: Existing task_log entries with multi-character assistant content like "Here is the result" or "def main():"
  When: The log viewer renders
  Then: Those entries are classified as visible and displayed normally

TEST-15: Multi-character punctuation is not classified as noise
  Given: Existing task_log entries with content like "...", "---", or ">>>"
  When: The log viewer renders
  Then: Those entries are classified as visible (the single-char check only applies to length-1 strings)

TEST-16: Noise count reflects newly classified entries
  Given: A task log containing 3 single-punctuation entries and 2 "Tool completed" tool_result entries
  When: The log viewer renders with noise hidden
  Then: The "Show all (N)" button count includes these 5 additional noise entries

TEST-17: UI build succeeds
  Given: The isNoiseText and classifyEntry changes are applied
  When: Running npm run build in the ui directory
  Then: The build completes with no errors

## Out of Scope

- Engine-side filtering of tool_result events (ENGINE-057 only filters assistant/thinking chunks; tool_result "Tool completed" is handled UI-side by UI-060)
- Retroactive database cleanup of existing noise entries (both issues handle noise at their respective layers going forward; UI-060 also hides existing noise from the DB)
- Configurable noise filter rules or user-facing filter settings
- Filtering multi-character markdown fragments like "```" or "---" (already handled by the existing isNoiseText regex)
- Log compression or deduplication beyond noise filtering

## Integration Points

- ENGINE-057 and UI-060 are independent defense-in-depth layers. They do not share types or interfaces.
- ENGINE-057 prevents new noise from being stored in the database (source-level filter in acp_client.py).
- UI-060 hides noise that already exists in the database OR that slips past future engine-side filters (render-level filter in LogViewer.tsx).
- Both issues use the same definition of "noise single character": a single character where `not char.isalnum()` (Python) / `/[^a-zA-Z0-9]/` (TypeScript). This alignment is by convention, not by shared code.
- The shared data path is: ACP bridge -> task_logs table -> REST API GET /api/runs/:id/tasks/:id/logs -> LogViewer component. ENGINE-057 filters before the first step; UI-060 filters at the last step.

## Done Criteria

This sprint is complete when:
- All acceptance tests PASS in the evaluator's verdict
- `uv run pytest` passes with no regressions
- `uv run ruff check .` and `uv run pyright` pass
- `cd ui && npm run build` succeeds
- `cd ui && npm run lint` passes
