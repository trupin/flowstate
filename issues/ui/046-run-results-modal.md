# [UI-046] Add "View Results" button + modal showing run output

## Domain
ui

## Status
done

## Priority
P1

## Dependencies
- Depends on: SERVER-017
- Blocks: —

## Summary
Add a "View Results" button on completed runs that opens a modal showing the pipeline output. The modal displays: (1) a git diff if the workspace is a repo, or (2) a list of created/modified files otherwise. Always shows task summaries from each node. Uses the `GET /api/runs/{run_id}/results` endpoint.

## Acceptance Criteria
- [ ] "View Results" button visible on completed/failed runs in RunDetail header
- [ ] Button NOT shown on running/pending runs
- [ ] Clicking opens a modal with:
  - Git diff (syntax-highlighted, scrollable) when `git_available` is true
  - File change list when `file_changes` is present
  - Task summaries section with per-node SUMMARY.md rendered as markdown
- [ ] Modal closes on Escape key or clicking outside
- [ ] Loading state while fetching results

## Technical Design

### Files to Create/Modify
- `ui/src/api/types.ts` — Add `RunResults` interface
- `ui/src/api/client.ts` — Add `runs.getResults(id)` method
- `ui/src/components/ResultsModal/ResultsModal.tsx` — New modal component
- `ui/src/components/ResultsModal/ResultsModal.css` — Styles
- `ui/src/pages/RunDetail.tsx` — Add "View Results" button

### RunResults Type
```typescript
export interface RunResults {
    workspace: string | null;
    git_available: boolean;
    git_diff: string | null;
    file_changes: { path: string; status: string }[] | null;
    task_summaries: Record<string, string>;
}
```

### ResultsModal Component
- Reuse TaskModal pattern (overlay, Escape to close)
- Three sections:
  1. **Diff** tab (when git_available): `<pre>` with the unified diff, monospace font
  2. **Files** tab (when file_changes): list with status icons (created/modified/deleted)
  3. **Summaries** tab: one collapsible section per node with markdown-rendered SUMMARY.md
- Tab navigation at the top

### Button Placement
In RunDetail.tsx header, next to the status badge, for terminal statuses.

## Testing Strategy
- Visual verification with Playwright
- Verify modal opens and shows diff content for git-based runs
- Verify modal shows file list for non-git runs

## Completion Checklist
- [ ] Component created
- [ ] Wired to RunDetail
- [ ] Visual verification
- [ ] `/lint` passes
