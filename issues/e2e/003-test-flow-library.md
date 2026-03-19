# [E2E-003] Test: Flow Library

## Domain
e2e

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: E2E-002, UI-010, SERVER-002
- Blocks: E2E-010, E2E-014

## Spec References
- specs.md Section 10.1 — "Pages" (Flow Library)
- specs.md Section 10.7 — "Sidebar"

## Summary
E2E tests for the Flow Library page: discovering flows from the watched directory, displaying validity status, showing graph previews, and handling parse/type-check errors with error banners.

## Acceptance Criteria
- [ ] `tests/e2e/test_flow_library.py` exists with 4-5 test functions
- [ ] Tests verify flow discovery (write .flow to watch_dir, sidebar shows it)
- [ ] Tests verify validity indicators (green dot for valid, error dot for invalid)
- [ ] Tests verify graph preview renders nodes when a valid flow is clicked
- [ ] Tests verify error banner appears for flows with type errors
- [ ] All tests pass: `uv run pytest tests/e2e/test_flow_library.py -v`

## Technical Design

### Tests

1. **test_discover_valid_flow** — Write LINEAR_FLOW to watch_dir, wait_for_flow_discovery(), verify `[data-testid="sidebar-flow-{name}"]` is visible with `data-status="valid"`.

2. **test_discover_invalid_flow** — Write INVALID_FLOW to watch_dir, wait for sidebar entry with `data-status="error"`.

3. **test_flow_graph_preview** — Write LINEAR_FLOW, click sidebar entry, verify `[data-testid="node-start"]`, `[data-testid="node-work"]`, `[data-testid="node-done"]` are visible.

4. **test_type_error_shows_banner** — Write FLOW_WITH_TYPE_ERROR, click sidebar entry, verify `[data-testid="error-banner"]` is visible and contains error text.

5. **test_multiple_flows_listed** — Write 2 valid + 1 invalid flow, verify all 3 appear in sidebar with correct status indicators.

### Assertion Pattern
```python
from playwright.sync_api import expect
write_flow(watch_dir, "my_flow.flow", LINEAR_FLOW, workspace)
wait_for_flow_discovery(base_url, "my_flow")
page.goto(base_url)
flow_entry = page.locator('[data-testid="sidebar-flow-my_flow"]')
expect(flow_entry).to_be_visible(timeout=5000)
expect(flow_entry).to_have_attribute("data-status", "valid")
```

## Testing Strategy
Each test writes flow files, polls the API for discovery, then uses Playwright to verify UI state. All waits use explicit timeouts via `expect()`.
