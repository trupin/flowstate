# [UI-014] Flow Watcher Hook (live file change events)

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: UI-008, UI-009
- Blocks: UI-003, UI-010

## Spec References
- specs.md Section 10.8 — "File Watcher"
- specs.md Section 10.3 — "WebSocket Protocol" (flow.file_changed, flow.file_error, flow.file_valid events)
- agents/05-ui.md — "Flow Watcher Hook (`useFlowWatcher.ts`)"

## Summary
Create a React hook that listens for file system change events from the backend via WebSocket and maintains a live list of discovered flows with their current validity status. When the backend detects changes to `.flow` files (via its file watcher), it pushes `flow.file_changed`, `flow.file_error`, and `flow.file_valid` events. This hook processes those events and triggers re-fetches of `GET /api/flows` to keep the UI in sync with the filesystem. The Sidebar and Flow Library both consume this hook.

## Acceptance Criteria
- [ ] `ui/src/hooks/useFlowWatcher.ts` exists and exports the `useFlowWatcher` hook
- [ ] Hook returns `{ flows, isConnected }` where `flows` is `DiscoveredFlow[]`
- [ ] Hook fetches `GET /api/flows` on mount for initial state
- [ ] Hook listens for `flow.file_changed` WebSocket events and re-fetches flows
- [ ] Hook listens for `flow.file_error` WebSocket events and re-fetches flows
- [ ] Hook listens for `flow.file_valid` WebSocket events and re-fetches flows
- [ ] Hook maintains a stable reference for `flows` when data hasn't changed (to prevent unnecessary re-renders)
- [ ] Hook exposes `isConnected` from the underlying WebSocket
- [ ] Multiple components using `useFlowWatcher` share the same data (or each instance independently fetches — acceptable for MVP)

## Technical Design

### Files to Create/Modify
- `ui/src/hooks/useFlowWatcher.ts` — flow watcher hook

### Key Implementation Details

#### Hook implementation

```typescript
import { useState, useEffect, useRef } from 'react';
import { useWebSocket } from './useWebSocket';
import { api } from '../api/client';
import type { DiscoveredFlow, FlowEvent } from '../api/types';

interface UseFlowWatcherReturn {
    flows: DiscoveredFlow[];
    isConnected: boolean;
}

export function useFlowWatcher(): UseFlowWatcherReturn {
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
    const { lastEvent, isConnected } = useWebSocket(wsUrl);
    const [flows, setFlows] = useState<DiscoveredFlow[]>([]);
    const fetchingRef = useRef(false);

    // Initial fetch
    useEffect(() => {
        fetchFlows();
    }, []);

    // Re-fetch when relevant file watcher events arrive
    useEffect(() => {
        if (!lastEvent) return;

        const relevantEvents = [
            'flow.file_changed',
            'flow.file_error',
            'flow.file_valid',
        ];

        if (relevantEvents.includes(lastEvent.type)) {
            fetchFlows();
        }
    }, [lastEvent]);

    async function fetchFlows() {
        // Debounce: skip if a fetch is already in-flight
        if (fetchingRef.current) return;
        fetchingRef.current = true;

        try {
            const result = await api.flows.list();
            setFlows(result);
        } catch (err) {
            // On error, keep the previous flows list
            console.error('Failed to fetch flows:', err);
        } finally {
            fetchingRef.current = false;
        }
    }

    return { flows, isConnected };
}
```

#### Event types handled

From specs.md Section 10.3:

| Event | Payload | Trigger |
|-------|---------|---------|
| `flow.file_changed` | `{ file_path, flow_name }` | A `.flow` file was modified on disk |
| `flow.file_error` | `{ file_path, flow_name, errors: [...] }` | A `.flow` file has parse/type errors after change |
| `flow.file_valid` | `{ file_path, flow_name }` | A previously broken `.flow` file is now valid |

All three events trigger a re-fetch of `GET /api/flows`. The re-fetch approach is simpler than trying to apply granular updates from event payloads (which may not contain all the fields needed by `DiscoveredFlow`). The REST API is the source of truth.

#### WebSocket subscription model

The flow watcher events are **broadcast** events — they are not scoped to a specific `flow_run_id`. The WebSocket connection receives these events without needing to subscribe to a particular run. The `useWebSocket` hook receives all events on the connection; this hook filters for the relevant event types.

If the backend requires a subscription for file watcher events, a general `subscribe` message may need to be sent on connect. For MVP, assume these events are broadcast to all connected clients.

#### Debouncing

When multiple files change rapidly (e.g., user saves several files in quick succession, or a bulk file operation), multiple events arrive in rapid succession. The `fetchingRef` acts as a simple debounce: if a fetch is already in-flight, skip subsequent triggers. The fetch result will reflect the latest state of all files.

For more aggressive debouncing (e.g., wait 200ms after the last event before fetching), a `setTimeout`-based debounce can be added:

```typescript
const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

function debouncedFetchFlows() {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(fetchFlows, 200);
}
```

For MVP, the simple in-flight guard is sufficient.

#### Shared state consideration

If both the Sidebar and FlowLibrary use `useFlowWatcher()`, they each create independent instances with independent WebSocket connections and REST fetches. For MVP, this duplication is acceptable (two connections, two fetches). In Phase 3, this could be optimized by:
- Lifting the hook state to a shared context provider
- Using a state management library (Zustand, Jotai, etc.)
- Creating a singleton WebSocket manager

For now, keep it simple: each hook instance is independent.

### Edge Cases
- Backend not running when hook mounts — initial fetch fails, flows stays empty; WebSocket reconnect loop retries
- WebSocket connected but backend file watcher not running — no events arrive; initial fetch still works
- File deleted from disk — `flow.file_changed` event fires; re-fetch returns updated list without the deleted flow
- New file added to watched directory — `flow.file_changed` event fires; re-fetch includes the new flow
- File changed but is syntactically identical — event still fires, re-fetch returns same data (React sets state to same array, but reference changes; `setFlows` triggers re-render)
- Very many `.flow` files (50+) — `GET /api/flows` returns all of them; acceptable for MVP
- Event arrives between unmount and cleanup — `fetchingRef` and `setFlows` may fire after unmount; add a mounted guard if React strict mode warnings appear:

```typescript
const mountedRef = useRef(true);
useEffect(() => {
    return () => { mountedRef.current = false; };
}, []);

// In fetchFlows:
if (mountedRef.current) setFlows(result);
```

## Testing Strategy
1. Hook returns empty `flows` array initially
2. After mount, flows are populated from API response (mock `fetch`)
3. When a `flow.file_changed` event arrives, flows are re-fetched
4. When a `flow.file_error` event arrives, flows are re-fetched
5. When a `flow.file_valid` event arrives, flows are re-fetched
6. Irrelevant events (e.g., `task.started`) do NOT trigger a re-fetch
