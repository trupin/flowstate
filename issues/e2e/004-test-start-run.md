# [E2E-004] Test: Start Run

## Domain
e2e

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: E2E-002, UI-012, SERVER-003
- Blocks: —

## Spec References
- specs.md Section 10.9 — "Start Run Modal"

## Summary
E2E tests for the Start Run flow: opening the modal from Flow Library, verifying parameter form generation (text inputs, number inputs, checkboxes with defaults), submitting, and verifying navigation to Run Detail.

## Acceptance Criteria
- [ ] `tests/e2e/test_start_run.py` exists with 3-4 test functions
- [ ] Tests verify modal opens with correct flow name
- [ ] Tests verify parameter inputs are rendered with correct types and defaults
- [ ] Tests verify submitting the form starts a run and navigates to Run Detail
- [ ] All tests pass: `uv run pytest tests/e2e/test_start_run.py -v`

## Technical Design

### Tests

1. **test_modal_opens** — Write PARAMETERIZED_FLOW, click flow in sidebar, click "Start Run" button, verify `[data-testid="start-run-modal"]` is visible.

2. **test_param_form_renders** — Verify `[data-testid="param-focus"]` is a text input with default "all", `[data-testid="param-verbose"]` is a checkbox (unchecked by default).

3. **test_start_run_navigates** — Configure mock for all nodes as success, fill params, click `[data-testid="start-run-btn"]`, verify page navigates to Run Detail (graph view visible).

4. **test_start_run_with_custom_params** — Fill focus="auth", check verbose=true, start run, verify flow begins executing (node starts running).

## Testing Strategy
Uses PARAMETERIZED_FLOW which declares `param focus: string = "all"` and `param verbose: bool = false`. Mock subprocess configured with NodeBehavior.success() for all nodes.
