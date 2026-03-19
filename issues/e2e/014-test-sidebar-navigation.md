# [E2E-014] Test: Sidebar Navigation

## Domain
e2e

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: E2E-003, UI-003
- Blocks: —

## Spec References
- specs.md Section 10.7 — "Sidebar"

## Summary
E2E tests for sidebar navigation: clicking flows navigates to Flow Library with that flow selected, clicking active runs navigates to Run Detail, and the sidebar correctly reflects active runs.

## Acceptance Criteria
- [ ] `tests/e2e/test_sidebar_navigation.py` exists with 2-3 test functions
- [ ] Tests verify clicking a flow in sidebar shows its graph preview
- [ ] Tests verify an active run appears in sidebar and clicking it opens Run Detail
- [ ] All tests pass: `uv run pytest tests/e2e/test_sidebar_navigation.py -v`

## Technical Design

### Tests

1. **test_click_flow_shows_preview** — Write two flows, verify both in sidebar, click second flow, verify graph preview updates to show that flow's nodes.

2. **test_active_run_in_sidebar** — Start a flow (with gate to keep it running), verify `[data-testid="sidebar-run-{id}"]` appears in ACTIVE RUNS section with status color indicator.

3. **test_click_active_run_opens_detail** — With a running flow in sidebar, click it, verify Run Detail page loads with the graph and live status for that run.

## Testing Strategy
Uses LINEAR_FLOW for simple navigation tests. For active run tests, a gate holds execution so the run stays visible in the sidebar long enough to click it.
