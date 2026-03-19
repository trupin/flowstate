# [E2E-010] Test: File Watcher

## Domain
e2e

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: E2E-003, SERVER-006, UI-013
- Blocks: —

## Spec References
- specs.md Section 10.8 — "File Watcher"
- specs.md Section 10.3 — "WebSocket Protocol" (flow.file_changed, flow.file_error, flow.file_valid)

## Summary
E2E tests for the file watcher: verifying the UI auto-updates when .flow files change on disk, error banners appear/disappear correctly, and new files are discovered.

## Acceptance Criteria
- [ ] `tests/e2e/test_file_watcher.py` exists with 3-4 test functions
- [ ] Tests verify modifying a valid flow to invalid shows error banner
- [ ] Tests verify fixing an invalid flow back to valid removes error banner
- [ ] Tests verify adding a new .flow file updates the sidebar
- [ ] All tests use polling (wait_for_flow_discovery) not time.sleep for assertions
- [ ] All tests pass: `uv run pytest tests/e2e/test_file_watcher.py -v`

## Technical Design

### Tests

1. **test_modify_valid_to_invalid** — Write LINEAR_FLOW, wait for discovery, verify valid. Overwrite with INVALID_FLOW, wait for error state in API, verify `[data-testid="error-banner"]` appears.

2. **test_fix_invalid_to_valid** — Start with INVALID_FLOW, wait for error state. Overwrite with LINEAR_FLOW, poll API until valid, verify error banner disappears and graph preview renders.

3. **test_add_new_flow** — Start with one flow in watch_dir, verify sidebar shows 1 flow. Write second flow, poll API until 2 flows discovered, verify both in sidebar.

4. **test_delete_flow** — Start with a flow, verify visible. Delete the file, poll API until flow disappears, verify removed from sidebar.

## Testing Strategy
File watcher has inherent latency (~1-2s for watchfiles). Use `wait_for_flow_discovery()` helper that polls `GET /api/flows` instead of sleeping. For error state, poll until the flow's `is_valid` field changes. Timeouts of 5-10s for file watcher operations.
