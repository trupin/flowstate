# [UI-023] Replace Flows list panel with selected flow detail view

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: UI-010
- Blocks: none

## Summary
The "Flows" panel in the main content area currently shows a redundant list of flows that duplicates the sidebar. Instead, when a flow is selected in the sidebar, this panel should show a rich detail view of the selected flow: its settings (budget, context mode, error policy, workspace, schedule, judge mode), node/edge summary, parameters, recent run history, and the DSL source. This gives users at-a-glance understanding of a flow's configuration without needing to read the raw `.flow` file.

## Acceptance Criteria
- [ ] Selecting a flow in the sidebar shows its detail in the main panel (not a list of all flows)
- [ ] Flow settings displayed: budget, context mode, on_error policy, workspace, skip_permissions, judge, schedule, on_overlap
- [ ] Node summary: count of entry/task/exit nodes, list of node names with types
- [ ] Edge summary: count and types (unconditional, conditional, fork, join)
- [ ] Parameters listed with name, type, and default value
- [ ] Recent runs section: last 5 runs with status, started_at, elapsed time (clickable to navigate to run detail)
- [ ] DSL source shown in a collapsible code block (syntax highlighted or monospace)
- [ ] If no flow is selected, show a placeholder ("Select a flow from the sidebar")

## Technical Design

### Files to Modify
- `ui/src/components/FlowLibrary/` (or equivalent) — replace list view with detail view
- `ui/src/types.ts` — the API already returns full flow data including settings in the `/api/flows` response; may need `/api/flows/:id` for source DSL

### Key Implementation Details

**Flow settings section** — extract from the API response (already available via `GET /api/flows`):
- Budget: format duration (e.g., "30m", "1h")
- Context: "handoff" / "session" / "none"
- On error: "pause" / "abort" / "skip"
- Workspace: path or "not set"
- Judge: "enabled" / "disabled" (default)
- Schedule: cron expression or "none"
- Skip permissions: yes/no

**Node/edge summary** — derive from the `nodes` and `edges` arrays in the flow response.

**Recent runs** — fetch from `GET /api/runs?flow_name=<name>` (or filter client-side from all runs). Show as a compact list with status badges.

**DSL source** — available via `GET /api/flows/:id` as `source_dsl`. Show in a `<pre><code>` block, collapsible.

### Edge Cases
- Flow with validation errors: show errors prominently with settings still visible
- Flow with no runs yet: "No runs yet" in recent runs section
- Very long DSL source: collapsible by default, expand on click
- Flow deleted while viewing: handle gracefully (show "Flow not found")

## Testing Strategy
- Visual verification: select a flow, verify all settings are displayed correctly
- Compare displayed settings with raw .flow file to confirm accuracy
- Test with flows that have parameters, schedules, and various settings
