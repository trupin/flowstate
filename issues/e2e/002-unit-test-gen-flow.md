# [E2E-002] Create mocked integration test for unit test generation flow

## Domain
e2e

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: —

## Summary
Create a comprehensive integration test (with mocked AI agents) for a realistic unit test generation flow. The flow models a pipeline where: a developer submits a Jira ticket, Flowstate analyzes the code for defects, the developer decides how to handle defects (fix/skip/escalate), Flowstate generates tests, opens a PR, auto-remediates CI failures (up to 3 attempts), and either delivers the PR for review or escalates.

This tests the full engine with complex conditional routing, retry loops, fence synchronization, and multi-path convergence — all with MockSubprocessManager (no real Claude Code).

## Flow Structure

```
entry: receive_ticket
  ↓
task: analyze_code
  ↓
[conditional] defects_found?
  ├── "defects found" → task: developer_decision
  │     ├── "fix first" → task: fix_defects
  │     ├── "skip fixes" → task: skip_defects
  │     └── "escalate" → task: escalate_defects
  │     (all merge →)
  └── "no defects" → task: generate_tests
  ↓
(merge) → task: open_pr
  ↓
[conditional] pr_checks_pass?
  ├── "checks pass" → task: pr_ready
  └── "checks fail" → task: auto_remediate_1
        ↓
        [conditional] fixed_after_1?
        ├── "fixed" → task: pr_ready
        └── "not fixed" → task: auto_remediate_2
              ↓
              [conditional] fixed_after_2?
              ├── "fixed" → task: pr_ready
              └── "not fixed" → task: auto_remediate_3
                    ↓
                    [conditional] fixed_after_3?
                    ├── "fixed" → task: pr_ready
                    └── "not fixed" → task: escalate_to_adapt
exit: ticket_closed
```

## Acceptance Criteria
- [ ] Flow DSL parses and type-checks successfully
- [ ] Happy path: no defects → generate tests → PR passes → ticket closed
- [ ] Defect path: defects found → developer fixes → generate tests → PR passes
- [ ] Retry path: PR fails → auto-remediate 1 → fixed → PR ready
- [ ] Escalation path: PR fails 3 times → escalate to ADAPT
- [ ] All paths eventually reach the exit node
- [ ] MockSubprocessManager routes each node to the correct outcome

## Technical Design

### Files to Create

**`tests/e2e/flows/unit_test_gen.flow`** — The full flow DSL with all nodes and conditional edges.

**`tests/e2e/test_unit_test_gen_flow.py`** — Integration test using:
- `MockSubprocessManager` with pre-programmed responses per node
- `FlowstateDB` with in-memory SQLite
- `FlowExecutor` running the flow
- Multiple test cases routing through different paths

### MockSubprocessManager Configuration

The mock should return different exit codes and outputs per node to drive routing:

```python
# Happy path: no defects, PR passes first time
mock_mgr.task_responses = {
    "receive_ticket": (0, []),
    "analyze_code": (0, []),     # DECISION.json → "no defects"
    "generate_tests": (0, []),
    "open_pr": (0, []),          # DECISION.json → "checks pass"
    "pr_ready": (0, []),
    "ticket_closed": (0, []),
}

# Defect + retry path
mock_mgr.task_responses = {
    "analyze_code": (0, []),     # DECISION.json → "defects found"
    "developer_decision": (0, []),  # DECISION.json → "fix first"
    "fix_defects": (0, []),
    "generate_tests": (0, []),
    "open_pr": (0, []),          # DECISION.json → "checks fail"
    "auto_remediate_1": (0, []), # DECISION.json → "not fixed"
    "auto_remediate_2": (0, []), # DECISION.json → "fixed"
    "pr_ready": (0, []),
    "ticket_closed": (0, []),
}
```

### Key Test Cases

1. **test_happy_path** — No defects, tests generated, PR passes → ticket closed
2. **test_defect_fix_path** — Defects found, developer chooses fix, tests generated, PR passes
3. **test_defect_skip_path** — Defects found, developer skips, tests generated
4. **test_defect_escalate_path** — Defects found, escalated to owner
5. **test_pr_retry_once** — PR fails, auto-remediate succeeds on attempt 1
6. **test_pr_retry_three_times_then_escalate** — PR fails 3 times, escalated to ADAPT
7. **test_full_flow_all_nodes_reachable** — Verify no dead-end nodes

## Testing Strategy
- `uv run pytest tests/e2e/test_unit_test_gen_flow.py -v`
- All tests use MockSubprocessManager — no real Claude, no API calls
- Tests verify: final flow status, task execution history, node traversal order
