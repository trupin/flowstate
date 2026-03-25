# [ENGINE-041] Harden: Subtask API error handling and validation

## Domain
engine

## Status
todo

## Priority
P2

## Dependencies
- Depends on: ENGINE-040, SERVER-015
- Blocks: none

## Spec References
- specs.md Section 14 — "Agent Subtask Management"

## Summary
Follow-up hardening for the agent subtask management feature. The initial implementation (ENGINE-040 + SERVER-015) provides the basic happy path. This issue covers graceful error handling in the agent prompt instructions (advising agents what to do if API calls fail), server-side rate limiting for subtask creation, subtask title length validation, and ensuring subtask count is bounded per task execution.

## Acceptance Criteria
- [ ] Prompt instructions include brief error handling guidance (e.g., "If the API returns an error, continue your work — subtask tracking is optional")
- [ ] Server validates subtask title is non-empty and ≤ 200 characters
- [ ] Server limits subtasks to 50 per task execution (returns 400 beyond limit)
- [ ] Subtask creation returns proper error responses the agent can parse

## Technical Design
TBD — refine after ENGINE-040 and SERVER-015 are implemented.

## Testing Strategy
- Test title validation (empty, too long)
- Test subtask count limit enforcement
- Test error response format is parseable

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
