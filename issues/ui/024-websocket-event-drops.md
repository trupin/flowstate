# [UI-024] WebSocket events dropped during rapid state transitions

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: —

## Summary
When a node completes and the next node starts, the UI graph does not update in real-time — the user must reload the page to see the current state. The root cause is that `useWebSocket` stores only a single `lastEvent` via `useState`. In React 18, `setState` calls from WebSocket `onmessage` handlers are automatically batched. When multiple events arrive in quick succession (e.g., `task.completed` → `edge.transition` → `task.started`), only the last event is processed by the `useEffect` that calls `applyEvent`. Intermediate events are silently dropped, leaving the graph in a stale state.

## Acceptance Criteria
- [ ] All WebSocket events are processed, even when they arrive in rapid succession
- [ ] Graph nodes update status in real-time as tasks complete and start
- [ ] Edge transitions animate correctly during execution
- [ ] No events are dropped under any timing scenario
- [ ] Existing reconnection and replay logic still works

## Technical Design

### Files to Modify
- `ui/src/hooks/useWebSocket.ts` — replace single `lastEvent` with an event queue
- `ui/src/hooks/useFlowRun.ts` — process all queued events, not just the latest

### Key Implementation Details

**Root cause**: `useWebSocket.ts` line 50:
```typescript
setLastEvent(data);  // Only stores the LAST event — previous ones are lost
```

React 18 batches `setState` calls from WebSocket handlers. If 3 events arrive before React renders, only the 3rd value of `lastEvent` is seen by the `useEffect` in `useFlowRun.ts`.

**Fix — Option A (event queue)**:

Replace `lastEvent: FlowEvent | null` with an accumulating queue:

```typescript
// useWebSocket.ts
const [eventQueue, setEventQueue] = useState<FlowEvent[]>([]);

ws.onmessage = (event) => {
  const data = JSON.parse(event.data) as FlowEvent;
  lastTimestampRef.current = data.timestamp;
  setEventQueue(prev => [...prev, data]);
};

// Return eventQueue instead of lastEvent
```

```typescript
// useFlowRun.ts
useEffect(() => {
  if (ws.eventQueue.length === 0) return;
  for (const event of ws.eventQueue) {
    if (event.flow_run_id !== runId) continue;
    applyEvent(event, setRun, setTasks, setEdges, setLogs, fetchRunDetail);
  }
  ws.clearQueue();  // or setEventQueue([])
}, [ws.eventQueue]);
```

**Fix — Option B (ref-based callback)**:

Instead of state, use a callback ref that `useFlowRun` registers:

```typescript
// useWebSocket.ts
const onEventRef = useRef<((event: FlowEvent) => void) | null>(null);

ws.onmessage = (event) => {
  const data = JSON.parse(event.data) as FlowEvent;
  lastTimestampRef.current = data.timestamp;
  onEventRef.current?.(data);  // Direct callback — no state batching
};

// Expose setOnEvent to let consumers register
```

This bypasses React state entirely and calls the handler synchronously for each message. No batching, no drops.

**Recommended: Option A** — it's simpler, uses standard React patterns, and the queue naturally handles burst events. Option B is faster but couples the hook more tightly.

### Edge Cases
- Events arriving during initial page load (before subscribe completes)
- WebSocket reconnect replaying many events at once
- Very long-running flows generating thousands of events (queue memory)
- Two events with the same timestamp

## Testing Strategy
- Start a 3-node linear flow, verify all 3 nodes show status updates in real-time
- Monitor WebSocket messages in browser devtools to confirm events arrive
- Check that task.completed → edge.transition → task.started sequence is fully processed
- E2E: `/e2e linear` should show all transitions without reload
