# [ENGINE-021] Remove OrchestratorManager and simplify executor

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-019, ENGINE-020
- Blocks: ENGINE-022

## Summary
Remove the OrchestratorManager and all orchestrator-related code paths from the executor. The thin-agent model means the Python executor drives everything directly via SDKRunner. This removes ~60 lines from _execute_single_task and deletes orchestrator.py (185 lines).

## Acceptance Criteria
- [ ] orchestrator.py deleted
- [ ] orchestrator_mgr parameter removed from FlowExecutor constructor
- [ ] Orchestrator code paths removed from _execute_single_task
- [ ] Orchestrator session handling removed from _handle_conditional and _handle_default_edge
- [ ] build_orchestrator_system_prompt removed from context.py
- [ ] serialize_flow_graph removed from context.py (only used by orchestrator)
- [ ] Orchestrator session endpoints removed from routes.py
- [ ] OrchestratorManager import removed from routes.py

## Technical Design

### Files to Modify/Delete
- Delete `src/flowstate/engine/orchestrator.py`
- `src/flowstate/engine/executor.py` — remove orchestrator_mgr param, orchestrator imports, orchestrator code paths, session ID persistence
- `src/flowstate/engine/context.py` — remove build_orchestrator_system_prompt(), serialize_flow_graph()
- `src/flowstate/engine/judge.py` — remove evaluate_via_orchestrator(), OrchestratorSession import
- `src/flowstate/server/routes.py` — remove OrchestratorManager import, orchestrator_mgr creation, orchestrator session endpoints
- `src/flowstate/server/models.py` — remove OrchestratorSession, OrchestratorLogEntry, OrchestratorLogsResponse

## Testing Strategy
- All engine tests pass without orchestrator
