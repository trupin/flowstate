# [UI-041] Display `subtasks` attribute in flow settings panel

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: none (server already serializes all AST fields via `_serialize_flow()`)
- Blocks: none

## Spec References
- specs.md Section 14.1 — "Overview" (agent subtask management)

## Summary
The `subtasks` boolean attribute (controlling agent subtask management) is not visible in the flow detail settings panel. Add it to the settings grid alongside the other flow attributes (budget, context, on_error, etc.). Also add the missing `subtasks` field to the `FlowAstJson` TypeScript interface and the other missing attributes (`harness`, `worktree`, `max_parallel`) that the server already provides.

## Acceptance Criteria
- [ ] `FlowAstJson` interface includes `subtasks: boolean` field
- [ ] `FlowAstJson` interface includes `harness: string`, `worktree: boolean`, and `max_parallel: number` fields (they're already serialized by the server but missing from the type)
- [ ] Flow settings panel shows "Subtasks" with "enabled"/"disabled" value
- [ ] Flow settings panel shows "Harness", "Worktree", and "Max Parallel" values
- [ ] Values display correctly for flows with and without the attribute set

## Technical Design

### Files to Modify
- `ui/src/api/types.ts` — Add missing fields to `FlowAstJson`
- `ui/src/components/FlowDetailPanel/FlowDetailPanel.tsx` — Add settings rows

### Key Implementation Details

**types.ts** — Add to `FlowAstJson`:
```typescript
subtasks: boolean;
harness: string;
worktree: boolean;
max_parallel: number;
```

**FlowDetailPanel.tsx** — Add after the existing "On Overlap" row (around line 193):
```tsx
<span className="flow-settings-key">Subtasks</span>
<span className="flow-settings-value">{ast.subtasks ? 'enabled' : 'disabled'}</span>
<span className="flow-settings-key">Harness</span>
<span className="flow-settings-value">{ast.harness}</span>
<span className="flow-settings-key">Worktree</span>
<span className="flow-settings-value">{ast.worktree ? 'enabled' : 'disabled'}</span>
{ast.max_parallel > 1 && (
  <>
    <span className="flow-settings-key">Max Parallel</span>
    <span className="flow-settings-value">{ast.max_parallel}</span>
  </>
)}
```

### Edge Cases
- `max_parallel` defaults to 1, which is the common case — only display when > 1 to avoid noise
- `harness` defaults to "claude" — always display since users may want to confirm which harness is active

## Testing Strategy
- Build succeeds: `cd ui && npm run build`
- Lint passes: `cd ui && npm run lint`
- Manual verification: load a flow with `subtasks = true` and confirm the settings panel shows "Subtasks: enabled"

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
