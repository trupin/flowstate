# Sprint 022

**Issues**: UI-062, UI-063
**Domains**: ui
**Date**: 2026-03-27

## Acceptance Tests

### UI-062: Show harness provider in flow detail and document configuration

TEST-1: Harness attribute visible in flow settings panel
  Given: A flow loaded in the UI with the default harness ("claude")
  When: The user selects the flow in the Flow Library and views the settings panel
  Then: A "Harness" row is displayed in the settings grid with the value "claude"

TEST-2: Non-default harness value displayed correctly
  Given: A flow whose DSL sets `harness = "gemini"` at the flow level
  When: The user views that flow's settings panel
  Then: The "Harness" row displays "gemini" (not "claude" or blank)

TEST-3: Help tooltip for harness configuration
  Given: The flow settings panel is visible with the Harness row
  When: The user hovers over or clicks an info icon/tooltip trigger near the Harness field
  Then: A tooltip or info text appears explaining how to configure providers: mentions `flowstate.toml`, the `[harnesses.<name>]` section format, `command` key, and optional `env` key

TEST-4: Per-node harness override shown in node details
  Given: A flow where a specific node has `harness = "custom"` overriding the flow default of "claude"
  When: The user views the AST JSON nodes section (either in the settings panel or node details in the log viewer)
  Then: That node's details display the harness value "custom" indicating it overrides the flow default

TEST-5: Flow with no explicit harness shows default
  Given: A flow whose DSL does not set a harness attribute (relying on the default)
  When: The user views the flow settings panel
  Then: The Harness row shows "claude" (the default value)

TEST-6: UI build passes after changes
  Given: All UI-062 code changes are applied
  When: Running `npm run build` in the ui directory
  Then: The build completes with no errors

### UI-063: Make tool calls verbose-only and show full tool details

TEST-7: Tool call entries hidden by default
  Given: A run with task logs containing tool_use and tool_result entries (e.g., Read, Edit, Bash tool calls)
  When: The user navigates to the run detail and selects a task, viewing the log viewer with Verbose mode off (default)
  Then: Tool call entries (tool_use and their paired tool_result) are not displayed in the log list

TEST-8: Tool call entries visible in verbose mode
  Given: A run with task logs containing tool_use and tool_result entries
  When: The user clicks the "Verbose" toggle button in the log viewer toolbar
  Then: Tool call entries appear in the log list, displayed as grouped tool call blocks with tool name, input parameters, and result output

TEST-9: Subtask API tool calls remain visible without verbose mode
  Given: A run with task logs containing tool_use entries for subtask API calls (POST or PATCH to a URL containing "/subtasks")
  When: The user views the log viewer with Verbose mode off (default)
  Then: Subtask tool call entries remain visible (rendered as subtask events with title and status), not filtered out as noise

TEST-10: Tool call blocks show tool name and primary input parameter
  Given: A run with tool_use entries that include input data (e.g., file_path for Read, command for Bash)
  When: The user enables Verbose mode and views the tool call blocks
  Then: Each tool call block displays the tool name as a badge and an inline summary showing the primary parameter value (e.g., "Read(src/main.py)" or "Bash(git status)")

TEST-11: Tool call blocks show result output
  Given: A run with grouped tool_use + tool_result entries where the tool_result contains actual output text
  When: The user enables Verbose mode and expands a tool call block
  Then: The expanded block shows both the input parameters and the result content

TEST-12: ACP-format tool data properly extracted
  Given: A run with ACP-originated tool_use entries containing `title`, `raw_input` (JSON string or object), and corresponding tool_result entries with `raw_output` or `content`
  When: The user enables Verbose mode and views these tool call blocks
  Then: The tool name is extracted from `title`, input parameters are extracted from `raw_input`, and the result is extracted from `raw_output`/`content` -- not showing empty "{}" or bare tool name repetition

TEST-13: Claude Code format tool calls still render correctly
  Given: A run with Claude Code stream-json format tool_use entries (nested `message.content[].type === "tool_use"` with `name`, `input`, `id`)
  When: The user enables Verbose mode and views these tool call blocks
  Then: Tool calls render with the correct tool name, input parameters, and result -- no regression from the existing behavior

TEST-14: Grouped tool calls classified as unit
  Given: A run where a tool_use entry is immediately followed by its paired tool_result entry
  When: The classification system determines visibility for this pair
  Then: Both entries in the group share the same classification -- if the tool_use is noise (non-subtask), the grouped block is noise; if the tool_use is a subtask call, the grouped block is visible

TEST-15: Noise count includes newly classified tool call entries
  Given: A task log containing 5 tool_use entries (3 non-subtask, 2 subtask) with matching tool_result entries
  When: The log viewer renders with Verbose mode off
  Then: The 3 non-subtask tool call groups are excluded from the visible list, and the 2 subtask groups remain visible

TEST-16: Tool calls with empty input render gracefully
  Given: A tool_use entry where the input is empty ({}) or raw_input is absent
  When: The user enables Verbose mode and views this tool call block
  Then: The tool call block displays the tool name without crashing and shows the summary as just the tool name (no parenthesized arguments)

TEST-17: UI build passes after changes
  Given: All UI-063 code changes are applied
  When: Running `npm run build` in the ui directory
  Then: The build completes with no errors

## Out of Scope

- Server-side API changes to expose per-node harness overrides (UI-062 uses data already available in `ast_json.nodes[*].harness` from `dataclasses.asdict()` serialization)
- Configurable noise filter rules or user-facing filter settings (tool call noise classification is hardcoded)
- Filtering tool calls by tool name or category (all non-subtask tool calls are treated uniformly as noise)
- Harness management UI (creating/editing harnesses from the UI -- configuration is file-based via `flowstate.toml`)
- Mobile-responsive layout for the tooltip or settings panel

## Integration Points

- Both issues are UI-only. No cross-domain integration is needed.
- UI-062 reads `ast_json.harness` (flow-level) and `ast_json.nodes[*].harness` (per-node) from the existing API response -- no server changes required.
- UI-063 modifies the `classifyEntry()` function that was recently updated in sprint 001 (UI-060). The new tool_use/tool_result noise classification adds to (not replaces) the existing noise rules for single-character fragments and "Tool completed" results.
- UI-063 interacts with the `groupLogEntries()` function which pairs tool_use with tool_result entries. The classification must happen before grouping in the filter pipeline so that noise tool calls are excluded from `filteredLogs` when Verbose is off.

## Done Criteria

This sprint is complete when:
- All acceptance tests PASS in the evaluator's verdict
- `cd ui && npm run build` succeeds
- `cd ui && npm run lint` passes
- No regressions in existing log viewer behavior (assistant messages, thinking blocks, subtask events continue to render correctly)
