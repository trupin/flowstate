# [UI-065] Show lumon security indicator in flow detail panel

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-021
- Blocks: —

## Spec References
- specs.md Section 9.9 — "Lumon Security Layer"

## Summary
Display a "Lumon" security badge in the flow detail panel, following the same pattern as the existing "Sandboxed" badge added in UI-064. Show the lumon config path if specified. Detect and display node-level lumon overrides (same pattern as harness overrides).

## Acceptance Criteria
- [ ] Flow detail panel shows a "Lumon" badge when `lumon = true`
- [ ] Badge shows lumon_config path below it if specified
- [ ] Node-level lumon overrides are detected and displayed
- [ ] Badge is not shown when `lumon = false` (default)
- [ ] All existing tests still pass

## Technical Design

### Files to Create/Modify
- `ui/src/components/FlowDetailPanel/FlowDetailPanel.tsx` — add lumon badge and override detection

### Key Implementation Details

Follow the exact pattern of the sandbox badge (UI-064):

1. In the flow settings section, after the sandbox badge, add:
```tsx
{ast.lumon && (
  <div className={styles.settingBadge}>
    Lumon
    {ast.lumon_config && (
      <span className={styles.settingDetail}>{ast.lumon_config}</span>
    )}
  </div>
)}
```

2. In the `useFlowDetailData()` hook (or equivalent), detect node-level lumon overrides:
```typescript
const hasLumonOverride = node.lumon != null && node.lumon !== ast.lumon;
```

3. Display override indicator on nodes that override the flow-level setting, following the same visual pattern as harness overrides.

### Edge Cases
- Both sandbox and lumon enabled → both badges shown
- Node overrides lumon to false while flow has lumon=true → show "Lumon: off" on node
- lumon_config path is very long → CSS should truncate with ellipsis

## Testing Strategy
- Verify component renders without crashing with lumon enabled/disabled
- Visual verification in browser

## E2E Verification Plan

### Verification Steps
1. Create a `.flow` file with `lumon = true` and `lumon_config = "security/strict.lumon.json"`
2. Start server + UI: `uv run flowstate serve` and `cd ui && npm run dev`
3. Open the flow in the browser
4. Verify the "Lumon" badge appears in the flow detail panel
5. Verify the config path is shown

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in: server restarted, exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
