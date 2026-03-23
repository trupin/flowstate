# [ENGINE-019] Create SDKRunner with Message→StreamEvent conversion

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: none
- Blocks: ENGINE-020, ENGINE-021, ENGINE-022, ENGINE-023, SERVER-010

## Summary
Create `SDKRunner` class that replaces `SubprocessManager` using the `claude-agent-sdk` package. Implements the same interface (run_task, run_task_resume, run_judge, kill) but uses the SDK's `query()` function internally. Includes a message-to-StreamEvent conversion layer so the executor's event processing requires zero changes.

## Acceptance Criteria
- [ ] SDKRunner implements run_task(), run_task_resume(), run_judge(), kill()
- [ ] SDK Message objects are converted to StreamEvent types correctly
- [ ] A synthetic process_exit SYSTEM event is emitted from ResultMessage
- [ ] JudgeResult is parsed from SDK output
- [ ] claude-agent-sdk>=0.1 added to pyproject.toml dependencies

## Technical Design

### Files to Create/Modify
- Create `src/flowstate/engine/sdk_runner.py` (~150 lines)
- Modify `pyproject.toml` — add `claude-agent-sdk>=0.1` dependency

### Key Implementation Details
- `run_task(prompt, workspace, session_id)` → `query(prompt, ClaudeAgentOptions(cwd=workspace))`
- `run_task_resume(prompt, workspace, session_id)` → `query(prompt, ClaudeAgentOptions(resume=True, session_id=session_id, cwd=workspace))`
- `run_judge(prompt, workspace)` → `query(prompt, ClaudeAgentOptions(model="sonnet", system_prompt=JUDGE_PROMPT, permission_mode="plan"))`
- Message→StreamEvent conversion: AssistantMessage→ASSISTANT, ToolUseBlock→TOOL_USE, ToolResultBlock→TOOL_RESULT, ResultMessage→RESULT
- Synthesize process_exit SYSTEM event at end of stream
- Re-export StreamEvent, StreamEventType, JudgeResult, SubprocessError, JudgeError from this module

## Testing Strategy
- Unit tests in tests/engine/test_sdk_runner.py for message conversion
