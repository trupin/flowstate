# [UI-042] Log viewer text overflows horizontally — long paths and tool badges break layout

## Domain
ui

## Status
done

## Priority
P1

## Dependencies
- Depends on: —
- Blocks: —

## Summary
Long text in the log viewer (file paths, tool call titles, terminal commands) overflows the panel width instead of wrapping or truncating. Visible in the screenshot: orange `Find` badges with long paths extend past the right edge of the log panel. The log panel also appears to shrink in width when expanding tool call details.

Two CSS issues:
1. `.log-content` (line 155 of LogViewer.css) has no `min-width: 0` or `overflow` constraint, so it can push the flex row wider than the container
2. `.log-line` (line 143) is `display: flex` but doesn't constrain its children's width — long content in `.log-content` forces the parent wider

## Acceptance Criteria
- [ ] Long file paths in tool call badges truncate with ellipsis instead of overflowing
- [ ] Log lines never extend beyond the log panel's right edge
- [ ] Expanding tool call details does not shrink the log panel width
- [ ] Pre-formatted blocks (code, terminal output) scroll horizontally within their box, not the whole panel

## Technical Design

### Files to Modify
- `ui/src/components/LogViewer/LogViewer.css` — Fix `.log-line` and `.log-content` overflow
- `ui/src/components/LogViewer/ToolCallBlock.css` — Fix `.tool-call-header` overflow for long tool names

### Key CSS Fixes

1. **`.log-line`** (line 143): Add `min-width: 0` to prevent flex children from overflowing:
   ```css
   .log-line {
     display: flex;
     gap: 8px;
     min-width: 0;
   }
   ```

2. **`.log-content`** (line 155): Add `min-width: 0` and `overflow: hidden`:
   ```css
   .log-content {
     color: var(--text-primary);
     min-width: 0;
     overflow: hidden;
   }
   ```

3. **`.log-viewer-content`** (line 132): Add `overflow-x: hidden` to prevent horizontal scroll on the log panel:
   ```css
   .log-viewer-content {
     ...
     overflow-x: hidden;
   }
   ```

4. **`.tool-call-header`** (line 13 of ToolCallBlock.css): Add `min-width: 0` and `overflow: hidden`:
   ```css
   .tool-call-header {
     ...
     min-width: 0;
     overflow: hidden;
   }
   ```

## Testing Strategy
- Visual: open a flow with long file paths in tool calls, verify no horizontal overflow
- Check tool call expansion doesn't shrink the panel
- Verify pre blocks still scroll horizontally within their container

## Completion Checklist
- [ ] CSS fixes applied
- [ ] Visual verification with running flow
- [ ] `/lint` passes (eslint, prettier)
