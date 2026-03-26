# [UI-044] Group consecutive thinking and assistant streaming chunks into single blocks

## Domain
ui

## Status
done

## Priority
P1

## Dependencies
- Depends on: UI-043
- Blocks: —

## Summary
ACP delivers thinking and assistant text as streaming chunks — each chunk is a separate log entry with a small fragment of text (e.g., "Now", " let me read the config", " files"). The `groupLogEntries()` function in `LogViewer.tsx` groups `tool_use`+`tool_result` pairs but treats each thinking/assistant chunk as an individual entry.

Results:
1. Many separate "Thoughts" blocks each showing a few words, instead of one block with the full reasoning text
2. Assistant messages split mid-word: "Bob" on one line, "'s Round 1 is done..." on the next — with different timestamps

## Acceptance Criteria
- [ ] Consecutive `thinking` entries are merged into a single ThinkingBlock with concatenated text
- [ ] Consecutive `assistant` entries are merged into a single assistant message with concatenated text
- [ ] Non-consecutive entries of the same type stay separate (e.g., thinking → tool_use → thinking = 2 thinking blocks)
- [ ] Tool call grouping still works correctly
- [ ] Existing rendering (timestamps, system entries, activity logs) unaffected

## Technical Design

### File to Modify
- `ui/src/components/LogViewer/LogViewer.tsx` — `groupLogEntries()` function (line ~680)

### Fix
After the `tool_use` grouping logic and before the `result.push({ type: 'single', entry })` fallback, add merging logic:

```typescript
// Merge consecutive thinking entries
if (parsed.kind === 'thinking') {
    // Check if the last group is also thinking — append text
    const last = result[result.length - 1];
    if (last?.type === 'merged_thinking') {
        last.text += parsed.text;
        last.ids.push(entry.id);
        i++;
        continue;
    }
    result.push({ type: 'merged_thinking', text: parsed.text, timestamp: entry.timestamp, ids: [entry.id] });
    i++;
    continue;
}

// Merge consecutive assistant entries
if (parsed.kind === 'assistant') {
    const last = result[result.length - 1];
    if (last?.type === 'merged_assistant') {
        last.text += parsed.text;
        last.ids.push(entry.id);
        i++;
        continue;
    }
    result.push({ type: 'merged_assistant', text: parsed.text, timestamp: entry.timestamp, ids: [entry.id] });
    i++;
    continue;
}
```

Then add rendering cases for `merged_thinking` and `merged_assistant` in the render function.

### Edge Cases
- Empty thinking chunks (text = "") should be skipped during merge
- Mixed thinking/assistant chunks should NOT be merged together
- A thinking block interrupted by a tool_use should start a new thinking block after

## Testing Strategy
- Visual: verify with Playwright at 1470x956 on the bob node of discuss_flowstate run
- Verify merged thinking shows as one expandable block
- Verify merged assistant shows as one markdown-rendered paragraph

## Completion Checklist
- [ ] Fix applied
- [ ] Visual verification
- [ ] `/lint` passes
