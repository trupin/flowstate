# [ENGINE-020] Replace SubprocessManager with SDKRunner in executor and judge

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-019
- Blocks: ENGINE-022

## Summary
Update all imports of SubprocessManager to use SDKRunner. Delete subprocess_mgr.py. The SDKRunner re-exports the same types (StreamEvent, StreamEventType, etc.) so downstream code changes are minimal — primarily import path updates.

## Acceptance Criteria
- [ ] All imports of SubprocessManager changed to SDKRunner
- [ ] subprocess_mgr.py deleted
- [ ] executor.py uses SDKRunner
- [ ] judge.py uses SDKRunner
- [ ] No references to SubprocessManager remain in engine code

## Technical Design

### Files to Modify
- `src/flowstate/engine/executor.py` — import SDKRunner instead of SubprocessManager
- `src/flowstate/engine/judge.py` — import from sdk_runner
- Delete `src/flowstate/engine/subprocess_mgr.py`

## Testing Strategy
- All existing tests pass with updated imports
