# [UI-066] Show decision and summary artifacts in node detail view

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-022
- Blocks: —

## Spec References
- specs.md Section 9.6 — "API-Based Artifact Protocol"
- specs.md Section 10.2 — "WebSocket Protocol" (optional: artifact_submitted event)

## Summary
Display task artifacts (DECISION.json content and SUMMARY.md content) in the node detail panel of the log viewer. When a user clicks on a completed node, they can see the decision the agent made (target, reasoning, confidence) and the summary the agent wrote. This makes routing decisions visible and debuggable from the UI.

## Acceptance Criteria
- [ ] Node detail panel shows "Decision" section when a `decision` artifact exists for the task
- [ ] Decision section displays: target node, reasoning text, confidence score (as percentage)
- [ ] Node detail panel shows "Summary" section when a `summary` artifact exists
- [ ] Summary section renders the markdown content as formatted text
- [ ] Artifacts are fetched from `GET /api/runs/{run_id}/tasks/{task_id}/artifacts/{name}`
- [ ] Artifacts load lazily when the node is selected (not on initial page load)
- [ ] Loading state shown while artifacts are being fetched
- [ ] No artifacts section shown for tasks without artifacts (clean UI)

## Technical Design

### Files to Create/Modify
- `ui/src/components/LogViewer/NodeDetailsPanel.tsx` — add artifact display sections
- `ui/src/hooks/useArtifacts.ts` — hook to fetch artifacts from API
- `ui/src/components/LogViewer/ArtifactDisplay.tsx` — artifact rendering component
- `ui/src/components/LogViewer/ArtifactDisplay.module.css` — styles

### Key Implementation Details

**useArtifacts hook:**
```typescript
interface Artifact {
  name: string;
  content: string;
  contentType: string;
}

function useArtifacts(runId: string, taskId: string): {
  decision: Artifact | null;
  summary: Artifact | null;
  loading: boolean;
}
```

Fetches `GET /api/runs/{runId}/tasks/{taskId}/artifacts/decision` and `.../summary` when the task is selected. Returns parsed content. Only fetches for completed/failed tasks.

**Decision display:**
- Parse JSON content: `{decision, reasoning, confidence}`
- Show target node name as a styled badge
- Show confidence as a colored bar (green > 0.8, yellow > 0.5, red < 0.5)
- Show reasoning as body text

**Summary display:**
- Render markdown content as HTML (use existing markdown rendering if available, or basic formatting)
- Truncate to first 500 chars with "Show more" expansion
- Monospace font for consistency with log viewer

**Integration with NodeDetailsPanel:**
- Add below existing metadata (node type, elapsed time, directories)
- Only show when task status is `completed` or `failed`
- Collapsible sections with headers "Decision" and "Summary"

### Edge Cases
- Task has no artifacts: show nothing (no empty state needed)
- Decision JSON is malformed: show raw content as code block
- Summary is very long: truncate with expand button
- Task is still running: don't fetch artifacts yet
- API returns 404: treat as "no artifact" (not an error)

## Testing Strategy
- Component renders without crashing when no artifacts
- Component shows decision when artifact API returns data
- Component handles malformed JSON gracefully
- Component truncates long summaries

## E2E Verification Plan

### Verification Steps
1. Start server and UI: `uv run flowstate server` + `cd ui && npm run dev`
2. Run a flow with conditional edges
3. After completion, click on a node that made a routing decision
4. Verify decision section shows target, reasoning, confidence
5. Click on any completed node
6. Verify summary section shows the agent's summary

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
