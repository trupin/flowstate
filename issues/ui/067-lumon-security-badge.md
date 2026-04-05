# [UI-067] Show Lumon security indicator in flow detail panel

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-025
- Blocks: —

## Spec References
- specs.md Section 9.9 — "Lumon Sandboxing"

## Summary
Display a "Lumon" security badge in the flow detail panel when `sandbox = true` or `lumon = true`. Show the lumon_config path if specified. Detect and display node-level overrides (e.g., a node with `lumon = false` in a flow with `lumon = true`).

## Acceptance Criteria
- [ ] Flow detail panel shows "Lumon" badge when `lumon` or `sandbox` is true
- [ ] Badge shows config path if `lumon_config` or `sandbox_policy` is set
- [ ] Node-level overrides are indicated (e.g., "Lumon disabled" on specific nodes)
- [ ] Consistent styling with existing badges (harness, subtasks, etc.)

## Technical Design

### Files to Modify

**`ui/src/components/FlowDetailPanel/FlowDetailPanel.tsx`:**
Follow the pattern of existing setting badges. Add:
```tsx
{(ast.lumon || ast.sandbox) && (
  <div className={styles.settingBadge}>
    Lumon
    {(ast.lumon_config || ast.sandbox_policy) && (
      <span className={styles.settingDetail}>
        {ast.lumon_config || ast.sandbox_policy}
      </span>
    )}
  </div>
)}
```

## Testing Strategy
- Component renders Lumon badge when lumon=true
- Component renders Lumon badge when sandbox=true
- No badge when both false

## E2E Verification Plan

### Verification Steps
1. Start server + UI dev
2. Load a flow with `sandbox = true`
3. Verify "Lumon" badge appears in flow detail panel

## E2E Verification Log
_[Agent fills this in]_

## Completion Checklist
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
