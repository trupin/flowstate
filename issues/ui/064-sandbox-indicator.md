# [UI-064] Show sandbox indicator in flow detail panel

## Domain
ui

## Status
todo

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: DSL-008
- Blocks: —

## Spec References
- specs.md Section 3.3 — "Flow Declaration" (sandbox attribute)

## Summary
Display a sandbox badge/indicator in the FlowDetailPanel when a flow has `sandbox = true`. The AST is already serialized to JSON and sent to the UI, so the new `sandbox` and `sandbox_policy` fields will appear automatically in the flow data. This issue adds a visual indicator so users can see at a glance whether a flow runs in a sandboxed environment.

## Acceptance Criteria
- [ ] FlowDetailPanel shows a "Sandboxed" badge when `flow.sandbox === true`
- [ ] Badge includes a tooltip explaining what sandbox mode means
- [ ] If `sandbox_policy` is set, tooltip shows the policy path
- [ ] No badge shown when `sandbox` is false or absent
- [ ] Styling consistent with existing badges (e.g., harness provider indicator)

## Technical Design

### Files to Create/Modify
- `ui/src/components/FlowDetailPanel/FlowDetailPanel.tsx` — add sandbox indicator
- `ui/src/components/FlowDetailPanel/FlowDetailPanel.css` — badge styling
- `ui/src/api/types.ts` — add sandbox fields to Flow type (if not already included via AST serialization)

### Key Implementation Details

Follow the pattern established by UI-062 (harness provider indicator). Add a sandbox badge in the flow metadata section of FlowDetailPanel:

```tsx
{flow.sandbox && (
  <span className={styles.badge} title={
    flow.sandbox_policy
      ? `Sandboxed (policy: ${flow.sandbox_policy})`
      : "Sandboxed — runs in OpenShell isolation"
  }>
    Sandbox
  </span>
)}
```

Check the existing harness badge implementation for exact styling patterns and tooltip approach.

### Edge Cases
- Flow data from older server versions without sandbox fields → treat undefined as false
- `sandbox_policy` is a long path → truncate in tooltip if needed

## Testing Strategy
- Component renders without crashing when `sandbox = true`
- Component renders without crashing when `sandbox = false` or absent
- Badge visible when sandbox is true, hidden when false

## E2E Verification Plan

### Verification Steps
1. Start dev server: `uv run flowstate serve` + `cd ui && npm run dev`
2. Create a `.flow` file with `sandbox = true`
3. Navigate to the flow in the UI
4. Verify "Sandbox" badge appears in the flow detail panel
5. Hover over badge — verify tooltip shows sandbox info

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
