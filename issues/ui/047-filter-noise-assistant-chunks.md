# [UI-047] Filter out noise assistant chunks (bare backticks, whitespace-only)

## Domain
ui

## Status
done

## Priority
P1

## Dependencies
- Depends on: UI-044
- Blocks: —

## Summary
Standalone assistant message chunks containing only markdown artifacts like triple backticks (` ``` `), bare `>` quote markers, or whitespace-only text appear as separate log lines. These are fragments from Claude's streaming output that got isolated between tool calls and don't merge with adjacent content. They should be classified as `hidden` so they don't clutter the console.

## Acceptance Criteria
- [ ] Assistant entries with only backticks (` ``` `, `` ` ``), whitespace, or bare markdown markers are hidden
- [ ] Assistant entries with real text content still render normally
- [ ] Merged assistant blocks that contain meaningful text are not affected

## Technical Design

### File to Modify
- `ui/src/components/LogViewer/LogViewer.tsx` — `classifyEntry()` function

### Fix
In `classifyEntry()`, after parsing, check if the content is noise:

```typescript
if (parsed.kind === 'assistant' && isNoiseText(parsed.text)) return 'hidden';
if (parsed.kind === 'thinking' && isNoiseText(parsed.text)) return 'hidden';

function isNoiseText(text: string): boolean {
    const trimmed = text.trim();
    if (trimmed === '') return true;
    // Bare markdown fences / quotes
    if (/^[`>*_~\-\s]+$/.test(trimmed)) return true;
    return false;
}
```

Also add the noise check inside `groupLogEntries()` before merging — skip empty/noise chunks so they don't get appended to merged blocks.

## Testing Strategy
- Visual: verify ` ``` ` lines no longer appear in the log viewer
- Verify real assistant text still renders

## Completion Checklist
- [ ] Fix applied
- [ ] Visual verification
- [ ] `/lint` passes
