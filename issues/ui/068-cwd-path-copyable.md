# [UI-068] Make CWD path fully visible and copyable in node details

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
The CWD path in the node details panel is truncated and not copyable. When worktrees use temp directories (e.g., `/var/folders/.../flowstate-86fae7c2-moderator-th809hh_`), the path is too long to display and the user can't click or copy it to navigate there.

## Acceptance Criteria
- [ ] CWD path is fully visible (wrap or tooltip on hover showing full path)
- [ ] CWD path is copyable (click-to-copy or select-all)
- [ ] Works for long temp directory paths

## Technical Design
- Show truncated path with tooltip on hover showing full path
- Add click-to-copy button (clipboard icon) next to the path
- Or: make the path element scrollable horizontally

## Testing Strategy
- Visual verification with long paths
