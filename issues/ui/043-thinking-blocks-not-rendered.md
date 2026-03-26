# [UI-043] Agent thinking/reasoning not shown — parseLogContent misses ACP thinking flag

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
Agent reasoning (thinking/thought content) is stored in the DB and sent by the API, but the UI's `parseLogContent()` in `LogViewer.tsx` doesn't detect it. The function looks for `content[].type === 'thinking'` blocks (Claude API format), but ACP sends thinking as `{"thinking": true, "message": {"content": [{"type": "text", "text": "..."}]}}` — the flag is at the top level, not in the content block type.

Result: thinking content falls through to regular assistant message rendering instead of the expandable `ThinkingBlock` component.

## Acceptance Criteria
- [ ] Thinking content renders in expandable ThinkingBlock (collapsible with "Thinking..."/"Thoughts" label)
- [ ] Regular assistant messages still render normally (no regression)
- [ ] Both ACP format (`thinking: true` at top level) and Claude API format (`type: "thinking"` in content blocks) are handled

## Technical Design

### File to Modify
- `ui/src/components/LogViewer/LogViewer.tsx` — `parseLogContent()` function

### Fix
In the assistant message parsing path, check the top-level `thinking` flag before falling through to text extraction:

```typescript
// In the 'assistant' case of parseLogContent():
const thinkingFlag = obj.thinking === true;
if (thinkingFlag) {
    // Extract text from message.content[].text (it's type='text', not type='thinking')
    const text = extractTextFromContent(message.content);
    if (text) {
        return { kind: 'thinking', text, done: true };
    }
}
```

## Testing Strategy
- Visual: open a completed run, verify thinking blocks appear as expandable sections
- Check that regular assistant messages still render as markdown

## Completion Checklist
- [ ] Fix applied
- [ ] Visual verification with Playwright
- [ ] `/lint` passes
