# [UI-019] Show and configure node working directories

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Spec References
- specs.md Section 3 — Node definitions (cwd attribute)
- specs.md Section 4.1 — S8: Every node must have a resolvable cwd

## Summary
Each node in the graph should display where the agent is working (its cwd). The DSL already supports per-node `cwd` attributes, but the UI doesn't surface this information. Users should be able to see at a glance which directory each agent operates in, and the graph visualization should reflect different workspaces visually.

## Acceptance Criteria
- [ ] Each node in the graph shows its working directory (either its own `cwd` or the flow's `workspace`)
- [ ] The working directory is visible in the compact node view (truncated path)
- [ ] The expanded node view shows the full path
- [ ] Different working directories are visually distinguishable (e.g., color-coded or grouped)
- [ ] In the run detail view, the active task's cwd is shown in the log viewer header
- [ ] The flow library view shows the flow-level workspace setting

## Technical Design

### Files to Create/Modify
- `ui/src/components/NodePill.tsx` — Show cwd in compact and expanded views
- `ui/src/components/NodePill.css` — Style for cwd display
- `ui/src/components/GraphView/GraphView.tsx` — Pass cwd data to nodes
- `ui/src/components/LogViewer/LogViewer.tsx` — Show cwd in log viewer header for selected task
- `ui/src/pages/FlowLibrary.tsx` — Show workspace in flow details

### Key Implementation Details
- **Node cwd resolution**: Each node's effective cwd is either its own `cwd` attribute or the flow's `workspace`. The API already returns `cwd` on both `FlowNodeDef` and `TaskExecution`.
- **Compact display**: Show last 2 path segments (e.g., `…/flowstate/src`) with full path on hover/tooltip.
- **Run detail**: The task execution response includes `cwd`. Show it in the log viewer header next to the task name.
- **DSL support**: The grammar already supports `cwd = "/path"` per node. No parser changes needed — just UI surfacing.

### Edge Cases
- Node with no explicit cwd — show the flow workspace with "(inherited)" label
- Very long paths — truncate with tooltip for full path
- Relative paths — resolve relative to workspace for display

## Testing Strategy
- Create a flow with different per-node cwds and verify they display correctly
- Check that inherited workspace shows "(inherited)" or similar indicator
- Verify cwd is visible in both flow library (definition) and run detail (execution) views
