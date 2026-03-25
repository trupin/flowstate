# [ENGINE-045] Default AcpHarness uses wrong command — needs claude-agent-acp

## Domain
engine

## Status
done

## Priority
P0

## Dependencies
- Depends on: ENGINE-044 (env + timeouts fix, done)
- Blocks: All E2E execution suites

## Spec References
- specs.md Section 4 — "Execution Engine"

## Summary
The default harness in `app.py:247` uses `AcpHarness(command=["claude"])`, but the `claude` CLI does not speak ACP JSON-RPC over stdio. The ACP adapter for Claude Code is a separate npm package: `@zed-industries/claude-agent-acp` (https://github.com/zed-industries/claude-agent-acp), which wraps the Claude Agent SDK in an ACP-compatible layer.

**Fix**: Change the command from `["claude"]` to `["claude-agent-acp"]` and ensure the package is installed.

## Acceptance Criteria
- [x] Default harness command changed to `claude-agent-acp`
- [x] E2E linear flow initializes ACP connection successfully

## Technical Design

### Files Modified
- `src/flowstate/server/app.py` — Change default AcpHarness command to `["claude-agent-acp"]`

### Prerequisites
- `npm install -g @zed-industries/claude-agent-acp` must be run on the host

## Completion Checklist
- [x] Code change made
- [x] `/lint` passes
