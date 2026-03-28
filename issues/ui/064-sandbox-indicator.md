# [UI-064] Show sandbox indicator in flow detail panel

## Domain
ui

## Status
in_progress

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

**Date**: 2026-03-27
**Environment**: macOS, Chromium via Playwright (1470x956 viewport), backend on port 8080, Vite dev on port 5174

**Steps performed**:

1. Started backend: `uv run flowstate server --port 8080`
2. Created `flows/sandbox_test.flow` with `sandbox = true` and `sandbox_policy = "policies/strict.yaml"`
3. Verified backend returns `sandbox: true` and `sandbox_policy: "policies/strict.yaml"` in `ast_json` via `curl -s http://localhost:8080/api/flows/sandbox_test`
4. Started UI: `cd ui && npm run dev -- --port 5174`
5. Opened browser via Playwright, clicked `sandbox_test` in sidebar
6. Verified sandbox badge:
   - `.flow-sandbox-badge` element found (count: 1)
   - Badge text: "SANDBOXED" (uppercase via CSS text-transform)
   - Badge title/tooltip: "Sandboxed (policy: policies/strict.yaml)"
   - Policy path shown inline: "(policies/strict.yaml)"
7. Navigated to `agent_delegation` flow (no sandbox attribute)
   - `.flow-sandbox-badge` element NOT found (count: 0) -- correct, no badge for non-sandboxed flows
8. Screenshot evidence saved at `/tmp/sandbox_detail.png` (badge visible) and `/tmp/sandbox_no_badge.png` (no badge)

**Build/Lint**:
- `npm run build`: PASS (tsc + vite build, 828 modules)
- `npm run lint`: PASS (eslint clean)
- `npx prettier --check "src/**/*.{ts,tsx}"`: PASS

**Conclusion**: All acceptance criteria verified. Badge appears only when `sandbox === true`, tooltip includes policy path when set, no badge when sandbox is false/absent, styling consistent with existing settings grid layout.

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
