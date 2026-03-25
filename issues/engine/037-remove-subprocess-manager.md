# [ENGINE-037] Harden: Remove SubprocessManager from task execution

## Domain
engine

## Status
todo

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: ENGINE-035
- Blocks: —

## Summary
After ENGINE-035 makes ACP the sole agent harness, SubprocessManager is dead code for task execution. Remove it to reduce maintenance burden and prevent confusion. Keep only if needed for internal tooling.

## Acceptance Criteria
- [ ] SubprocessManager is no longer imported or used for task execution in the executor
- [ ] `DEFAULT_HARNESS = "claude"` fallback to SubprocessManager is removed
- [ ] All references to SubprocessManager in routes, config, and server setup are removed
- [ ] Tests that mocked SubprocessManager for task execution are updated or removed
- [ ] subprocess_mgr.py is deleted or clearly marked as deprecated

## Testing Strategy
- `uv run pytest && uv run ruff check . && uv run pyright`
- Verify no import errors or missing references

## Completion Checklist
- [ ] `/lint` passes
- [ ] All tests pass
- [ ] No dead code remaining
