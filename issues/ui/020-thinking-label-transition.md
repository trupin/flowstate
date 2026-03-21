# [UI-020] Transition "Thinking..." label to "Thoughts" after thinking completes

## Domain
ui

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: UI-006
- Blocks: none

## Summary
The "Thinking..." animated label in the LogViewer never transitions to a completed state. Once the agent finishes thinking and subsequent messages arrive (assistant text, tool use, etc.), the thinking block should change its label from the animated "Thinking..." to a static "Thoughts" (or similar) to indicate the thinking phase is complete. Currently it stays as "Thinking..." with animated dots indefinitely, which is confusing.

## Acceptance Criteria
- [ ] While thinking is the last/latest entry, label shows animated "Thinking..."
- [ ] Once subsequent log entries appear after a thinking block, label transitions to static "Thoughts"
- [ ] The animated dots stop after transition
- [ ] Clicking the block still expands/collapses to show the full reasoning text
- [ ] Multiple thinking blocks in the same log stream each transition independently

## Technical Design

### Files to Modify
- `ui/src/components/LogViewer/LogViewer.tsx` — `ThinkingBlock` component needs an `isActive` prop
- `ui/src/components/LogViewer/LogViewer.css` — conditional styling for active vs completed thinking

### Key Implementation Details
- Add an `isActive` prop to `ThinkingBlock`: `{ text: string; isActive: boolean }`
- When `isActive` is true: show "Thinking" with animated dots (current behavior)
- When `isActive` is false: show "Thoughts" with no animation, slightly different styling (e.g., no dots, maybe a different icon/indicator)
- Determine `isActive` in the parent: a thinking entry is active only if it's the last entry in the log stream (no subsequent entries have arrived yet)
- In `LogEntryContent` or the grouping logic, pass `isActive` based on whether the entry is the last in the list

### Edge Cases
- Multiple thinking blocks: each should transition independently when the next non-thinking entry appears
- Thinking block that is the very last log entry (task still running): stays as "Thinking..."
- Empty thinking text: should still show the label but collapsing shows nothing

## Testing Strategy
- Visual verification: start a flow, observe "Thinking..." during execution, confirm it changes to "Thoughts" after completion
- Check that clicking still toggles the expanded content
