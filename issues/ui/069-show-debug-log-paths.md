# [UI-069] Show agent logs and server logs paths in run detail

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: —

## Summary
When debugging a flow run, users need to access the agent's sandbox-guard log and the server logs. The UI should show clickable paths to these locations so users can inspect them directly. For Lumon-sandboxed tasks, show the path to `.claude/hooks/sandbox-guard.log` in the worktree. For server logs, show where the server is logging to.

## Acceptance Criteria
- [ ] Node details panel shows "Debug logs" section when task has a worktree artifact
- [ ] Shows path to `.claude/hooks/sandbox-guard.log` (when Lumon active)
- [ ] All paths are copyable (click-to-copy)
- [ ] Server log location shown somewhere in the UI (e.g., footer or settings)

## Technical Design
- Read the `worktree` artifact to get the worktree path
- Construct log paths: `{worktree_path}/.claude/hooks/sandbox-guard.log`
- Use the existing `/api/open` endpoint to open paths in the user's IDE
- Add a "Debug" section below the existing metadata in NodeDetailsPanel

## Testing Strategy
- Visual verification with a Lumon-enabled flow
