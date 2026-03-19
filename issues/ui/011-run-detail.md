# [UI-011] Run Detail Page

## Domain
ui

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-004, UI-005, UI-006, UI-007, UI-008, UI-009
- Blocks: none

## Spec References
- specs.md Section 10.1 — "Pages" (Run Detail)
- specs.md Section 10.3 — "WebSocket Protocol"
- agents/05-ui.md — "Run Detail (`RunDetail.tsx`)" and "Layout"

## Summary
Create the Run Detail page — the primary operational view of the Flowstate UI. It combines the graph visualization (~60% width), log viewer (~40% width), and control panel (bottom bar) into a single three-panel layout with real-time updates via WebSocket. Clicking a node in the graph switches the log viewer to show that task's output. Control buttons send actions to pause, resume, cancel, retry, or skip tasks.

## Acceptance Criteria
- [ ] `ui/src/pages/RunDetail.tsx` exists and is rendered at route `/runs/:id`
- [ ] `ui/src/pages/RunDetail.css` exists with page-specific styles
- [ ] Page uses `useFlowRun(runId)` hook for all state management
- [ ] Three-panel layout: graph left (~60%), log viewer right (~40%), control panel bottom
- [ ] Graph updates in real-time as `task.started`, `task.completed`, `task.failed`, `edge.transition` events arrive
- [ ] Clicking a node in the graph selects it and switches the log viewer to that task's logs
- [ ] Log viewer streams real-time output from `task.log` events for the selected task
- [ ] Control panel shows correct buttons based on flow status and selected task status
- [ ] Control panel budget bar updates in real-time
- [ ] WebSocket subscribes on mount, unsubscribes on unmount
- [ ] Page handles initial loading state (before REST response)
- [ ] Page handles completed/failed/cancelled runs (read-only, final state)
- [ ] URL parameter `id` is used to fetch the correct run

## Technical Design

### Files to Create/Modify
- `ui/src/pages/RunDetail.tsx` — Run Detail page component
- `ui/src/pages/RunDetail.css` — page styles
- `ui/src/App.tsx` — wire up the `RunDetail` component to the `/runs/:id` route (replacing placeholder)

### Key Implementation Details

#### Component structure

```typescript
import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useFlowRun } from '../hooks/useFlowRun';
import { GraphView } from '../components/GraphView';
import { LogViewer } from '../components/LogViewer';
import { ControlPanel } from '../components/ControlPanel';
import { api } from '../api/client';
import type { TaskStatus } from '../api/types';
import './RunDetail.css';

export function RunDetail() {
    const { id } = useParams<{ id: string }>();
    const {
        run, tasks, edges, selectedTask, selectTask, logs, isConnected, send,
    } = useFlowRun(id!);

    // Build task status map for the graph
    const taskStatuses = useMemo(() => {
        const map = new Map<string, TaskStatus>();
        tasks.forEach((task, nodeName) => map.set(nodeName, task.status));
        return map;
    }, [tasks]);

    // Get logs for the selected task
    const selectedTaskExecution = selectedTask ? tasks.get(selectedTask) : null;
    const selectedLogs = selectedTaskExecution
        ? logs.get(selectedTaskExecution.id) ?? []
        : [];

    // Graph node/edge definitions from the flow
    const graphNodes = useMemo(() => {
        if (!run?.flow) return [];
        return run.flow.nodes.map(n => ({ name: n.name, type: n.type }));
    }, [run?.flow]);

    const graphEdges = useMemo(() => {
        if (!run?.flow) return [];
        return expandEdges(run.flow.edges);
    }, [run?.flow]);

    // Active edges (currently being traversed)
    const activeEdges = useMemo(() => {
        // An edge is "active" if the source task is completed and the target task is running
        const active = new Set<string>();
        tasks.forEach((task, nodeName) => {
            if (task.status === 'running') {
                // Find edges targeting this node
                graphEdges.forEach((e, i) => {
                    if (e.target === nodeName) {
                        active.add(`${e.source}-${e.target}-${i}`);
                    }
                });
            }
        });
        return active;
    }, [tasks, graphEdges]);

    // Wait-until map for waiting nodes
    const waitUntil = useMemo(() => {
        const map = new Map<string, string>();
        tasks.forEach((task, nodeName) => {
            if (task.status === 'waiting' && task.wait_until) {
                map.set(nodeName, task.wait_until);
            }
        });
        return map;
    }, [tasks]);

    // Control panel actions
    function handlePause() {
        send({ action: 'pause', flow_run_id: id, payload: {} });
    }

    function handleResume() {
        api.runs.resume(id!);
    }

    function handleCancel() {
        send({ action: 'cancel', flow_run_id: id, payload: {} });
    }

    function handleRetry(taskId: string) {
        send({ action: 'retry_task', flow_run_id: id, payload: { task_execution_id: taskId } });
    }

    function handleSkip(taskId: string) {
        send({ action: 'skip_task', flow_run_id: id, payload: { task_execution_id: taskId } });
    }

    function handleClearLogs() {
        // Client-side only: clear logs for the selected task from local state
        // This is handled by removing the entry from the logs map
        // For MVP, we can just track a "cleared at" timestamp and filter
    }

    if (!run) {
        return <div className="run-detail-loading">Loading run...</div>;
    }

    return (
        <div className="run-detail">
            <div className="run-detail-header">
                <h1>{run.flow_name} <span className="run-id">#{run.id.slice(0, 8)}</span></h1>
                <span className={`run-status status-${run.status}`}>{run.status}</span>
                {!isConnected && <span className="ws-disconnected">Reconnecting...</span>}
            </div>

            <div className="run-detail-main">
                <div className="run-detail-graph">
                    <GraphView
                        nodes={graphNodes}
                        edges={graphEdges}
                        taskStatuses={taskStatuses}
                        activeEdges={activeEdges}
                        waitUntil={waitUntil}
                        selectedNode={selectedTask}
                        onNodeClick={(nodeName) => selectTask(nodeName)}
                    />
                </div>

                <div className="run-detail-logs">
                    <LogViewer
                        logs={selectedLogs}
                        taskName={selectedTask}
                        onClear={handleClearLogs}
                    />
                </div>
            </div>

            <ControlPanel
                flowRunId={id!}
                flowStatus={run.status}
                elapsedSeconds={run.elapsed_seconds}
                budgetSeconds={run.budget_seconds}
                selectedTaskId={selectedTaskExecution?.id}
                selectedTaskStatus={selectedTaskExecution?.status}
                onPause={handlePause}
                onResume={handleResume}
                onCancel={handleCancel}
                onRetry={handleRetry}
                onSkip={handleSkip}
            />
        </div>
    );
}
```

#### Layout CSS

```css
.run-detail {
    display: flex;
    flex-direction: column;
    height: 100vh;
}

.run-detail-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 16px;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    min-height: 44px;
}

.run-detail-header h1 {
    font-size: 16px;
    margin: 0;
    font-weight: 600;
}

.run-id {
    color: var(--text-secondary);
    font-weight: 400;
    font-size: 13px;
}

.run-status {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 2px 8px;
    border-radius: 10px;
    font-weight: 500;
}

.run-status.status-running   { background: var(--status-running); color: #fff; }
.run-status.status-completed { background: var(--status-completed); color: #fff; }
.run-status.status-failed    { background: var(--status-failed); color: #fff; }
.run-status.status-paused    { background: var(--status-paused); color: #000; }
.run-status.status-cancelled { background: var(--text-secondary); color: #fff; }

.ws-disconnected {
    font-size: 11px;
    color: var(--warning);
    margin-left: auto;
}

.run-detail-main {
    display: flex;
    flex: 1;
    overflow: hidden;
}

.run-detail-graph {
    width: 60%;
    position: relative;
}

.run-detail-logs {
    width: 40%;
    position: relative;
}

.run-detail-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    color: var(--text-secondary);
}
```

#### Data flow

1. **On mount**: `useFlowRun(runId)` fetches `GET /api/runs/:id` for initial state (run details, tasks, edges, flow definition) and subscribes to WebSocket events.
2. **Real-time updates**: As events arrive via WebSocket, the `useFlowRun` hook updates its internal state. The component re-renders automatically.
3. **Node click → log viewer**: `selectTask(nodeName)` updates `selectedTask`. The component looks up the task execution ID from the tasks map and retrieves the corresponding logs.
4. **Control actions**: Button callbacks either send WebSocket messages (`send()`) or call REST API methods (`api.runs.*`).

#### Handling completed runs

When a run is finished (`completed`, `failed`, `cancelled`):
- Graph shows final node statuses
- Log viewer still shows logs for the selected task
- Control panel hides all action buttons (no pause/resume/cancel/retry/skip)
- Budget bar shows final state
- WebSocket may or may not be connected (no live events expected)

#### `expandEdges` helper

Same fork/join expansion logic as in UI-010:

```typescript
function expandEdges(edgeDefs: FlowEdgeDef[]) {
    const result = [];
    for (const e of edgeDefs) {
        if (e.edge_type === 'fork' && e.source && e.fork_targets) {
            for (const t of e.fork_targets) {
                result.push({ source: e.source, target: t, edgeType: 'fork' as const });
            }
        } else if (e.edge_type === 'join' && e.target && e.join_sources) {
            for (const s of e.join_sources) {
                result.push({ source: s, target: e.target, edgeType: 'join' as const });
            }
        } else if (e.source && e.target) {
            result.push({ source: e.source, target: e.target, edgeType: e.edge_type, condition: e.condition });
        }
    }
    return result;
}
```

This should be extracted to a shared utility (e.g., `ui/src/utils/edges.ts`) since both FlowLibrary and RunDetail need it.

### Edge Cases
- Run ID not found (404 from API) — show "Run not found" error
- Run has no tasks yet (just created) — graph shows all nodes as pending
- Very rapid events — React batches state updates; useMemo prevents unnecessary re-renders
- Selected task gets retried (new generation) — task execution ID changes; logs reset for the new execution
- WebSocket disconnects mid-run — "Reconnecting..." indicator shown; on reconnect, missed events are replayed
- User navigates away and back — new `useFlowRun` instance, fresh REST fetch + subscribe
- Log viewer clear then new logs arrive — new logs appear (clear is client-side only)
- Run with fork/join — multiple nodes may be running simultaneously; graph shows all running nodes with pulse animation

## Testing Strategy
Minimal for MVP:
1. Page renders loading state without crashing
2. Page renders with mock run data (all three panels visible)
3. Visual verification: graph fills 60%, logs fill 40%, control panel at bottom
4. Visual verification: clicking a node updates the log viewer header
