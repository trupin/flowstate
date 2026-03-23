# [ENGINE-022] Update all engine tests for new runner

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-019, ENGINE-020, ENGINE-021
- Blocks: none

## Summary
Replace MockSubprocessManager with MockSDKRunner in all test files. The mock conforms to the same interface and returns the same StreamEvent types, so test logic is unchanged — only the mock class name and imports update.

## Acceptance Criteria
- [ ] tests/engine/test_executor.py uses MockSDKRunner
- [ ] tests/engine/test_judge.py uses SDKRunner mock
- [ ] tests/e2e/conftest.py and mock_subprocess.py updated
- [ ] All tests pass

## Technical Design

### Files to Modify
- `tests/engine/test_executor.py` — rename MockSubprocessManager → MockSDKRunner, update imports
- `tests/engine/test_judge.py` — update mock imports
- `tests/e2e/conftest.py` — update mock import
- `tests/e2e/mock_subprocess.py` — rename class, update imports

## Testing Strategy
- Run full test suite: `uv run pytest`
