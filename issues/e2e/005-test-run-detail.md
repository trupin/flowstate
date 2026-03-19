# [E2E-005] Test: Run Detail

## Domain
e2e

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: E2E-002, UI-011, SERVER-005
- Blocks: E2E-006, E2E-007, E2E-008, E2E-009, E2E-011, E2E-012, E2E-013

## Spec References
- specs.md Section 10.1 — "Run Detail"
- specs.md Section 10.4 — "Graph Visualization"
- specs.md Section 10.5 — "Log Viewer"

## Summary
E2E tests for the Run Detail page: live graph visualization with real-time node status changes, streaming logs in the log viewer, flow completion status, and node selection changing log viewer content. This is the most important E2E test — it validates the full pipeline from flow execution through WebSocket events to UI rendering.

## Acceptance Criteria
- [ ] `tests/e2e/test_run_detail.py` exists with 4-5 test functions
- [ ] Tests verify nodes transition through statuses (pending → running → completed) visually
- [ ] Tests verify streaming log content appears in log viewer
- [ ] Tests verify flow status shows "Completed" when done
- [ ] Tests verify clicking a node changes log viewer content
- [ ] All tests pass: `uv run pytest tests/e2e/test_run_detail.py -v`

## Technical Design

### Tests

1. **test_nodes_transition_to_completed** — Write LINEAR_FLOW, configure all nodes as success, start run, wait for `[data-testid="node-start"][data-status="completed"]`, `node-work`, `node-done` all completed. Timeout 15s.

2. **test_streaming_logs_visible** — Configure "start" node with custom stream lines containing "Initializing project...", start run, click node-start, verify log viewer contains "Initializing project...".

3. **test_flow_status_completed** — Start linear flow, wait for `[data-testid="flow-status"]` to have text "Completed".

4. **test_click_node_changes_logs** — Start linear flow, wait for completion, click node-start → verify logs contain start's output, click node-work → verify logs change to work's output.

5. **test_running_node_has_pulse** — Use a gate on "work" node, start flow, verify `[data-testid="node-work"][data-status="running"]` is visible while gate holds, then release gate.

### Assertion Pattern
```python
mock_subprocess.configure_node("start", NodeBehavior.success("Initialized."))
mock_subprocess.configure_node("work", NodeBehavior.success("Work done."))
mock_subprocess.configure_node("done", NodeBehavior.success("Finalized."))
# ... start run via UI ...
expect(page.locator('[data-testid="node-done"][data-status="completed"]')).to_be_visible(timeout=15000)
```

## Testing Strategy
Uses LINEAR_FLOW with all nodes configured as NodeBehavior.success(). The mock returns deterministic output so log content can be asserted exactly. Timeouts are generous (15-20s) since mock tasks complete in ~100ms but WebSocket + React rendering adds latency.
