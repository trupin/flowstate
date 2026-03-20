# [SERVER-010] Update RunManager and routes to use SDKRunner

## Domain
server

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-019, ENGINE-020, ENGINE-021
- Blocks: none

## Summary
Update server code to use SDKRunner instead of SubprocessManager. Remove orchestrator session creation from routes. Update app.py to create SDKRunner instead of SubprocessManager.

## Acceptance Criteria
- [ ] app.py creates SDKRunner instead of SubprocessManager
- [ ] routes.py uses SDKRunner, removes orchestrator_mgr creation
- [ ] Orchestrator session endpoints removed from routes
- [ ] Server starts and runs correctly

## Technical Design

### Files to Modify
- `src/flowstate/server/app.py` — import SDKRunner, create it instead of SubprocessManager
- `src/flowstate/server/routes.py` — remove OrchestratorManager import, remove orchestrator_mgr creation, remove orchestrator endpoints

## Testing Strategy
- Server starts without errors
- E2E tests pass
