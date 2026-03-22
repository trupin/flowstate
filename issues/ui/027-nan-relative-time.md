# [UI-027] "NaNd ago" displayed for completed runs in flow detail panel

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: —

## Summary
The "Recent Runs" section in the flow detail panel shows "NaNd ago" instead of a relative time for completed runs. This happens when `run.created_at` is `undefined` or an unparseable timestamp. `new Date(undefined)` returns an Invalid Date whose `getTime()` is `NaN`, which propagates through the arithmetic to produce `${NaN}d ago`.

## Acceptance Criteria
- [ ] No "NaN" appears anywhere in the UI for time displays
- [ ] Runs with missing `created_at` show a fallback like "—" or "unknown"
- [ ] Runs with valid `created_at` show correct relative time

## Technical Design

### Files to Modify
- `ui/src/components/FlowDetailPanel/FlowDetailPanel.tsx` — guard `formatRelativeTime` and `formatElapsed` against invalid inputs

### Key Implementation Details

Add null/NaN guards:

```typescript
function formatRelativeTime(iso: string | undefined | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  // ... rest unchanged
}

function formatElapsed(seconds: number | undefined | null): string {
  if (seconds == null || isNaN(seconds)) return '—';
  // ... rest unchanged
}
```

Also check that `run.created_at` is populated in the API response. The `FlowRunRow` model has `created_at: str` but the list endpoint might not include it. Check `GET /api/runs` response.

## Testing Strategy
- Start and complete a flow, verify the recent runs show a valid relative time (not NaN)
- Visual verification in the flow detail panel
