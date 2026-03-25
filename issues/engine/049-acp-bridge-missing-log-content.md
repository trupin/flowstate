# [ENGINE-049] ACP bridge produces "Tool completed" instead of real log content

## Domain
engine

## Status
done

## Priority
P1

## Dependencies
- Depends on: ENGINE-045 (claude-agent-acp adapter, done)
- Blocks: —

## Spec References
- specs.md Section 9.2 — "Log Streaming Protocol"

## Summary
The log viewer shows only "Tool completed" for every tool execution instead of actual content. Two problems compound:

1. **`ToolCallProgress` events lack content**: The ACP bridge (`_map_acp_update_to_stream_event` in `acp_client.py:137-154`) maps `ToolCallProgress` to `TOOL_RESULT` StreamEvent, but the event only carries metadata (`tool_call_id`, `status`, `title`) — not the tool's actual output. The UI's `parseLogContent()` expects `message.content` or a `content` field, finds neither, and falls back to hardcoded "Tool completed" (`LogViewer.tsx:374-378`).

2. **Assistant text messages appear missing**: `AgentMessageChunk` events (which should carry the agent's text responses) are not showing up in the log viewer. Either `claude-agent-acp` isn't emitting them, or they're being lost in the pipeline. The result is a log viewer with only "Tool completed" entries and no actual readable content.

## Acceptance Criteria
- [ ] Tool execution logs show meaningful content (tool name, input summary, output/result) instead of "Tool completed"
- [ ] Assistant text messages appear in the log viewer between tool calls
- [ ] The log viewer provides a readable narrative of what the agent did

## Technical Design

### Phase 1: Debug what ACP events are actually arriving

Add temporary verbose logging to `_AcpBridgeClient.session_update()` in `acp_client.py` to log every event type and its content. Run a flow and inspect what `claude-agent-acp` actually sends:

```python
async def session_update(self, session_id, update, **kwargs):
    logger.info("ACP session_update: type=%s", type(update).__name__)
    # ... existing mapping logic
```

This will reveal which ACP event types are emitted and what data they carry.

### Phase 2: Fix the mapping based on findings

Likely outcomes:
- **If `AgentMessageChunk` events ARE arriving**: Fix whatever is preventing them from being stored/displayed
- **If `AgentMessageChunk` events are NOT arriving**: The `claude-agent-acp` adapter may not emit them for all content types. May need to use `ToolCallProgress.title` as a better fallback, or check if there's a richer event type available.
- **For tool results**: Extract `title` and `status` into a human-readable format instead of falling back to "Tool completed". E.g., "bash: completed" or use the tool title as the display text.

### Phase 3: UI fallback improvement

In `LogViewer.tsx:355-378`, improve the tool_result fallback to use available metadata:

```typescript
// Instead of hardcoded "Tool completed", use available metadata
const title = obj.title || 'Tool';
const status = obj.status || 'completed';
return {
    kind: 'tool_result',
    content: `${title}: ${status}`,
    summary: `${title}: ${status}`,
};
```

### Files to Modify
- `src/flowstate/engine/acp_client.py` — Add debug logging, potentially handle additional ACP event types
- `ui/src/components/LogViewer/LogViewer.tsx` — Improve tool_result fallback to use available metadata

### Edge Cases
- ACP event types may vary between `claude-agent-acp` versions
- Some tool calls may legitimately have no output (e.g., permission requests)

## Testing Strategy
1. Add debug logging, run a flow, inspect server logs for ACP event types
2. Verify assistant messages appear in log viewer
3. Verify tool entries show useful info instead of "Tool completed"
4. E2E: run `/e2e websocket` and verify log streaming shows real content

## Completion Checklist
- [ ] Root cause confirmed via debug logging
- [ ] ACP event mapping fixed
- [ ] UI fallback improved
- [ ] `/lint` passes
- [ ] Visual verification with running flow
