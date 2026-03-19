# [UI-008] WebSocket Hook + Flow Run State Hook

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-001
- Blocks: UI-003, UI-011, UI-013, UI-014

## Spec References
- specs.md Section 10.3 — "WebSocket Protocol"
- agents/05-ui.md — "WebSocket Hook (`useWebSocket.ts`)" and "Flow Run State Hook (`useFlowRun.ts`)"

## Summary
Create two React hooks that form the real-time data backbone of the UI. `useWebSocket` manages the WebSocket connection lifecycle with auto-reconnection and exponential backoff. `useFlowRun` combines an initial REST API fetch with live WebSocket event streaming to maintain the complete state of a flow run, including task statuses, edge transitions, and streaming logs.

## Acceptance Criteria
- [ ] `ui/src/hooks/useWebSocket.ts` exists and exports the `useWebSocket` hook
- [ ] `ui/src/hooks/useFlowRun.ts` exists and exports the `useFlowRun` hook
- [ ] **useWebSocket**: connects to `ws://localhost:<port>/ws` on mount
- [ ] **useWebSocket**: auto-reconnects on disconnect with exponential backoff (1s initial, doubling, 30s max)
- [ ] **useWebSocket**: on reconnect, sends `subscribe` with `last_event_timestamp` for event replay
- [ ] **useWebSocket**: exposes `send(data)` for sending client actions
- [ ] **useWebSocket**: exposes `subscribe(flowRunId)` and `unsubscribe(flowRunId)` convenience methods
- [ ] **useWebSocket**: exposes `isConnected` boolean state
- [ ] **useWebSocket**: exposes `lastEvent` — the most recently received event
- [ ] **useWebSocket**: cleans up (closes connection) on unmount
- [ ] **useFlowRun**: fetches `GET /api/runs/:id` on mount for initial state
- [ ] **useFlowRun**: subscribes to WebSocket events for `flow_run_id` on mount
- [ ] **useFlowRun**: unsubscribes on unmount
- [ ] **useFlowRun**: applies incoming events to local state (task status changes, new logs, edge transitions)
- [ ] **useFlowRun**: returns `{ run, tasks, edges, selectedTask, selectTask, logs, isConnected }`
- [ ] **useFlowRun**: `selectTask(nodeName)` updates which task's logs are shown
- [ ] **useFlowRun**: `logs` is a `Map<string, LogEntry[]>` keyed by task execution ID

## Technical Design

### Files to Create/Modify
- `ui/src/hooks/useWebSocket.ts` — WebSocket connection hook
- `ui/src/hooks/useFlowRun.ts` — flow run state management hook

### Key Implementation Details

#### `useWebSocket.ts`

```typescript
import { useRef, useState, useEffect, useCallback } from 'react';

interface UseWebSocketReturn {
    send: (data: unknown) => void;
    subscribe: (flowRunId: string, lastEventTimestamp?: string) => void;
    unsubscribe: (flowRunId: string) => void;
    lastEvent: FlowEvent | null;
    isConnected: boolean;
}

export function useWebSocket(url: string): UseWebSocketReturn {
    const wsRef = useRef<WebSocket | null>(null);
    const [isConnected, setIsConnected] = useState(false);
    const [lastEvent, setLastEvent] = useState<FlowEvent | null>(null);
    const lastTimestampRef = useRef<string | null>(null);
    const retryDelayRef = useRef(1000);
    const mountedRef = useRef(true);
    const subscribedRunsRef = useRef<Set<string>>(new Set());

    const connect = useCallback(() => {
        if (!mountedRef.current) return;

        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
            setIsConnected(true);
            retryDelayRef.current = 1000; // reset backoff

            // Re-subscribe to all previously subscribed runs
            subscribedRunsRef.current.forEach(runId => {
                ws.send(JSON.stringify({
                    action: 'subscribe',
                    flow_run_id: runId,
                    payload: {
                        flow_run_id: runId,
                        last_event_timestamp: lastTimestampRef.current ?? undefined,
                    },
                }));
            });
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data) as FlowEvent;
                lastTimestampRef.current = data.timestamp;
                setLastEvent(data);
            } catch {
                // ignore malformed messages
            }
        };

        ws.onclose = () => {
            setIsConnected(false);
            if (!mountedRef.current) return;

            // Reconnect with exponential backoff
            const delay = retryDelayRef.current;
            retryDelayRef.current = Math.min(delay * 2, 30000);
            setTimeout(connect, delay);
        };

        ws.onerror = () => {
            ws.close(); // triggers onclose → reconnect
        };
    }, [url]);

    useEffect(() => {
        mountedRef.current = true;
        connect();
        return () => {
            mountedRef.current = false;
            wsRef.current?.close();
        };
    }, [connect]);

    const send = useCallback((data: unknown) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify(data));
        }
    }, []);

    const subscribe = useCallback((flowRunId: string, lastEventTimestamp?: string) => {
        subscribedRunsRef.current.add(flowRunId);
        send({
            action: 'subscribe',
            flow_run_id: flowRunId,
            payload: {
                flow_run_id: flowRunId,
                last_event_timestamp: lastEventTimestamp,
            },
        });
    }, [send]);

    const unsubscribe = useCallback((flowRunId: string) => {
        subscribedRunsRef.current.delete(flowRunId);
        send({
            action: 'unsubscribe',
            flow_run_id: flowRunId,
            payload: { flow_run_id: flowRunId },
        });
    }, [send]);

    return { send, subscribe, unsubscribe, lastEvent, isConnected };
}
```

**Reconnection logic**:
1. On disconnect: wait `retryDelay` (starts at 1s), then reconnect
2. On each failure: double the delay (1s → 2s → 4s → 8s → 16s → 30s cap)
3. On successful reconnect: reset delay to 1s, re-send `subscribe` for all tracked runs with `last_event_timestamp`
4. Server replays all missed events after that timestamp

**WebSocket URL**: In development, Vite proxies `/ws` to the backend. The URL should be constructed relative to the current host:

```typescript
const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
```

#### `useFlowRun.ts`

```typescript
import { useState, useEffect, useCallback, useRef } from 'react';
import { useWebSocket } from './useWebSocket';
import { api } from '../api/client';
import type { FlowRun, TaskExecution, EdgeTransition, LogEntry, FlowEvent } from '../api/types';

interface UseFlowRunReturn {
    run: FlowRun | null;
    tasks: Map<string, TaskExecution>;
    edges: EdgeTransition[];
    selectedTask: string | null;
    selectTask: (nodeName: string | null) => void;
    logs: Map<string, LogEntry[]>;
    isConnected: boolean;
    send: (data: unknown) => void;
}

export function useFlowRun(runId: string): UseFlowRunReturn {
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
    const ws = useWebSocket(wsUrl);
    const [run, setRun] = useState<FlowRun | null>(null);
    const [tasks, setTasks] = useState<Map<string, TaskExecution>>(new Map());
    const [edges, setEdges] = useState<EdgeTransition[]>([]);
    const [selectedTask, setSelectedTask] = useState<string | null>(null);
    const [logs, setLogs] = useState<Map<string, LogEntry[]>>(new Map());

    // Initial fetch
    useEffect(() => {
        api.runs.get(runId).then(detail => {
            setRun(detail);
            const taskMap = new Map<string, TaskExecution>();
            detail.tasks?.forEach(t => taskMap.set(t.node_name, t));
            setTasks(taskMap);
            setEdges(detail.edges ?? []);
        });
    }, [runId]);

    // Subscribe to WebSocket
    useEffect(() => {
        ws.subscribe(runId);
        return () => ws.unsubscribe(runId);
    }, [runId, ws.subscribe, ws.unsubscribe]);

    // Process incoming events
    useEffect(() => {
        if (!ws.lastEvent || ws.lastEvent.flow_run_id !== runId) return;
        applyEvent(ws.lastEvent);
    }, [ws.lastEvent]);

    function applyEvent(event: FlowEvent) {
        const { type, payload } = event;
        switch (type) {
            case 'flow.status_changed':
                setRun(prev => prev ? { ...prev, status: payload.new_status } : prev);
                break;
            case 'flow.completed':
                setRun(prev => prev ? {
                    ...prev,
                    status: payload.final_status,
                    elapsed_seconds: payload.elapsed_seconds,
                } : prev);
                break;
            case 'flow.budget_warning':
                setRun(prev => prev ? {
                    ...prev,
                    elapsed_seconds: payload.elapsed_seconds,
                } : prev);
                break;
            case 'task.started':
                setTasks(prev => {
                    const next = new Map(prev);
                    next.set(payload.node_name, {
                        ...next.get(payload.node_name),
                        id: payload.task_execution_id,
                        node_name: payload.node_name,
                        status: 'running',
                        generation: payload.generation,
                    } as TaskExecution);
                    return next;
                });
                break;
            case 'task.completed':
                setTasks(prev => {
                    const next = new Map(prev);
                    const existing = next.get(payload.node_name);
                    if (existing) {
                        next.set(payload.node_name, {
                            ...existing,
                            status: 'completed',
                            elapsed_seconds: payload.elapsed_seconds,
                        });
                    }
                    return next;
                });
                break;
            case 'task.failed':
                setTasks(prev => {
                    const next = new Map(prev);
                    const existing = next.get(payload.node_name);
                    if (existing) {
                        next.set(payload.node_name, {
                            ...existing,
                            status: 'failed',
                            error_message: payload.error_message,
                        });
                    }
                    return next;
                });
                break;
            case 'task.log':
                setLogs(prev => {
                    const next = new Map(prev);
                    const taskLogs = next.get(payload.task_execution_id) ?? [];
                    next.set(payload.task_execution_id, [
                        ...taskLogs,
                        {
                            id: taskLogs.length,
                            task_execution_id: payload.task_execution_id,
                            content: payload.content,
                            log_type: payload.log_type,
                            timestamp: event.timestamp,
                        },
                    ]);
                    return next;
                });
                break;
            case 'task.waiting':
                setTasks(prev => {
                    const next = new Map(prev);
                    const existing = next.get(payload.node_name);
                    if (existing) {
                        next.set(payload.node_name, {
                            ...existing,
                            status: 'waiting',
                            wait_until: payload.wait_until,
                        });
                    }
                    return next;
                });
                break;
            case 'edge.transition':
                setEdges(prev => [...prev, {
                    id: `${payload.from_node}-${payload.to_node}-${Date.now()}`,
                    from_node: payload.from_node,
                    to_node: payload.to_node,
                    edge_type: payload.edge_type,
                    condition: payload.condition,
                    judge_reasoning: payload.judge_reasoning,
                } as EdgeTransition]);
                break;
        }
    }

    const selectTask = useCallback((nodeName: string | null) => {
        setSelectedTask(nodeName);
    }, []);

    return {
        run,
        tasks,
        edges,
        selectedTask,
        selectTask,
        logs,
        isConnected: ws.isConnected,
        send: ws.send,
    };
}
```

**Event processing**: The `applyEvent` function handles all server → client event types from specs.md Section 10.3. Each event type updates the appropriate piece of state:
- `flow.*` events → update `run` state
- `task.*` events → update `tasks` map and `logs` map
- `edge.*` events → append to `edges` array

**Logs keyed by task_execution_id**: The `logs` map uses `task_execution_id` as the key (not `node_name`) because a node may have multiple executions (generations). The parent component maps `selectedTask` (node name) to the correct `task_execution_id` via the `tasks` map.

### Edge Cases
- WebSocket connection fails on first attempt (backend not running) — reconnect loop handles this gracefully
- Component unmounts during reconnect timeout — `mountedRef` prevents state updates after unmount
- Multiple rapid events arrive — React batches state updates, each is processed in order
- Run ID changes (user navigates to a different run) — the `useEffect` dependencies on `runId` handle unsubscribe/resubscribe
- Event arrives for a task not yet in the tasks map — create a new entry with the event data
- Server replays events on reconnect — events are applied idempotently (status overwrite, log append is safe since IDs prevent true duplicates in practice; for MVP, minor duplicate logs on reconnect are acceptable)
- `lastEvent` changes but `flow_run_id` doesn't match — the event is ignored (could be from a different subscription)

## Testing Strategy
1. `useWebSocket`: test that it creates a WebSocket connection (mock `WebSocket` constructor)
2. `useWebSocket`: test that `isConnected` reflects connection state
3. `useWebSocket`: test that reconnect delay doubles up to 30s max
4. `useFlowRun`: test that initial state is empty, then populated after REST fetch
5. `useFlowRun`: test that incoming events update state correctly (mock WebSocket events)
