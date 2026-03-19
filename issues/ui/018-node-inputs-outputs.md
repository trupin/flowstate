# [UI-018] Show node inputs and outputs in run detail view

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
- specs.md Section 6 — Task execution and context assembly

## Summary
When viewing a completed run, each node should show its inputs (the prompt and context it received) and outputs (the result/SUMMARY.md it produced). When inputs or outputs reference files, the file path should be displayed and clickable to open the file directly in the browser.

## Acceptance Criteria
- [ ] Each node in the run detail view shows an "Inputs" section with the assembled prompt (or a truncated preview)
- [ ] Each node shows an "Outputs" section with the task result text and/or SUMMARY.md content
- [ ] File paths referenced in inputs/outputs are displayed and linkable
- [ ] Clicking a file path opens the file content in a modal or panel (read-only viewer)
- [ ] The task's working directory (cwd) is shown prominently on each node
- [ ] For handoff context mode, show which previous node's summary was included as context

## Technical Design

### Files to Create/Modify
- `ui/src/components/NodeDetail/` — New component for expanded node view with inputs/outputs
- `ui/src/pages/RunDetail.tsx` — Integrate NodeDetail when a node is selected
- `src/flowstate/server/routes.py` — Add endpoint to serve file contents: `GET /api/runs/:id/tasks/:tid/files/:path`

### Key Implementation Details
- **Inputs**: The assembled prompt is stored in `task_executions.prompt_text` in the DB. Expose via API.
- **Outputs**: The task result comes from the `result` stream event. SUMMARY.md is in the task directory (`~/.flowstate/runs/<run_id>/tasks/<node>-<gen>/SUMMARY.md`).
- **File viewer**: A simple endpoint that reads a file from the task's cwd and returns its content. Use syntax highlighting for code files.
- **Security**: Only serve files within the task's workspace directory. Validate paths to prevent directory traversal.

### Edge Cases
- Task that failed before producing output — show "No output" placeholder
- Very large prompt text — truncate with "Show full" toggle
- Binary files — show file path and size, don't try to render content

## Testing Strategy
- Complete a run and verify inputs/outputs are visible for each node
- Click a file path and verify the file viewer opens
- Verify handoff context shows the previous node's summary
