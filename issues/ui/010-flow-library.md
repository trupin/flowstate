# [UI-010] Flow Library Page

## Domain
ui

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-003, UI-004, UI-009, UI-014
- Blocks: UI-012, UI-013

## Spec References
- specs.md Section 10.1 — "Pages" (Flow Library)
- specs.md Section 10.8 — "File Watcher"
- agents/05-ui.md — "Flow Library (`FlowLibrary.tsx`)"

## Summary
Create the Flow Library page — the landing page of the Flowstate UI. It lists all discovered `.flow` files from the watched directory, shows their validity status, and provides a graph preview when a flow is selected. Valid flows have a "Start Run" button that opens the Start Run Modal (UI-012). Invalid flows show a persistent error banner (UI-013) with parse/type-check error details. The page auto-updates when files change on disk via the flow watcher hook.

## Acceptance Criteria
- [ ] `ui/src/pages/FlowLibrary.tsx` exists and is rendered at route `/`
- [ ] `ui/src/pages/FlowLibrary.css` exists with page-specific styles
- [ ] Page fetches flows from `GET /api/flows` on mount
- [ ] Flow list shows each flow's name, validity status (icon/dot), and last modified time
- [ ] Clicking a flow selects it and shows a graph preview in the main area
- [ ] Graph preview uses `GraphView` component in read-only mode
- [ ] Selected valid flow shows a "Start Run" button
- [ ] "Start Run" button opens the Start Run Modal (from UI-012)
- [ ] Selected invalid flow shows an error banner with error details (line numbers, messages, rule codes)
- [ ] Invalid flows preserve the last valid graph preview (if available) with the error banner overlaid
- [ ] Flow list auto-updates when files change (via `useFlowWatcher` from UI-014)
- [ ] URL state: selected flow ID is reflected in the URL (e.g., `/?flow=my_flow`) so sidebar links work
- [ ] No DSL editor, no upload, no delete buttons — flows are managed as files on disk

## Technical Design

### Files to Create/Modify
- `ui/src/pages/FlowLibrary.tsx` — Flow Library page component
- `ui/src/pages/FlowLibrary.css` — page styles
- `ui/src/App.tsx` — wire up the `FlowLibrary` component to the `/` route (replacing placeholder)

### Key Implementation Details

#### Component structure

```typescript
import { useState, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useFlowWatcher } from '../hooks/useFlowWatcher';
import { GraphView } from '../components/GraphView';
import { ErrorBanner } from '../components/ErrorBanner';
import type { DiscoveredFlow } from '../api/types';
import './FlowLibrary.css';

export function FlowLibrary() {
    const [searchParams, setSearchParams] = useSearchParams();
    const selectedFlowId = searchParams.get('flow');
    const { flows } = useFlowWatcher();  // auto-updating flow list
    const [selectedFlow, setSelectedFlow] = useState<DiscoveredFlow | null>(null);
    const [showStartModal, setShowStartModal] = useState(false);

    // When selectedFlowId changes or flows update, fetch the selected flow's full details
    useEffect(() => {
        if (selectedFlowId) {
            api.flows.get(selectedFlowId).then(setSelectedFlow).catch(() => setSelectedFlow(null));
        } else if (flows.length > 0 && !selectedFlowId) {
            // Auto-select the first flow if none selected
            setSearchParams({ flow: flows[0].id });
        }
    }, [selectedFlowId, flows]);

    function handleSelectFlow(flowId: string) {
        setSearchParams({ flow: flowId });
    }

    return (
        <div className="flow-library">
            <div className="flow-library-list">
                <h2>Flows</h2>
                {flows.length === 0 && (
                    <div className="flow-library-empty">
                        No flows discovered. Add <code>.flow</code> files to the watched directory.
                    </div>
                )}
                {flows.map(flow => (
                    <div
                        key={flow.id}
                        className={`flow-library-item ${flow.id === selectedFlowId ? 'selected' : ''}`}
                        onClick={() => handleSelectFlow(flow.id)}
                    >
                        <span className={`validity-dot ${flow.is_valid ? 'valid' : 'invalid'}`} />
                        <div className="flow-library-item-info">
                            <span className="flow-library-item-name">{flow.name}</span>
                            <span className="flow-library-item-modified">
                                {new Date(flow.last_modified).toLocaleString()}
                            </span>
                        </div>
                    </div>
                ))}
            </div>

            <div className="flow-library-preview">
                {selectedFlow ? (
                    <>
                        <div className="flow-library-preview-header">
                            <h2>{selectedFlow.name}</h2>
                            {selectedFlow.is_valid && (
                                <button
                                    className="start-run-btn"
                                    onClick={() => setShowStartModal(true)}
                                >
                                    Start Run
                                </button>
                            )}
                        </div>

                        {!selectedFlow.is_valid && (
                            <ErrorBanner errors={selectedFlow.errors} />
                        )}

                        <div className="flow-library-graph">
                            <GraphView
                                nodes={selectedFlow.nodes.map(n => ({
                                    name: n.name,
                                    type: n.type,
                                }))}
                                edges={selectedFlow.edges.map(e => ({
                                    source: e.source ?? '',
                                    target: e.target ?? '',
                                    edgeType: e.edge_type,
                                    condition: e.condition,
                                }))}
                                readOnly
                            />
                        </div>

                        {showStartModal && selectedFlow.is_valid && (
                            <StartRunModal
                                flow={selectedFlow}
                                onClose={() => setShowStartModal(false)}
                            />
                        )}
                    </>
                ) : (
                    <div className="flow-library-no-selection">
                        Select a flow to preview its graph
                    </div>
                )}
            </div>
        </div>
    );
}
```

#### Layout

The Flow Library page uses a two-column layout:
- **Left column (~250px)**: flow list with clickable items
- **Right column (remaining width)**: graph preview + error banner + start button

```css
.flow-library {
    display: flex;
    height: 100vh;
}

.flow-library-list {
    width: 250px;
    flex-shrink: 0;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    overflow-y: auto;
    padding: 16px 0;
}

.flow-library-list h2 {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-secondary);
    padding: 0 16px;
    margin: 0 0 12px 0;
}

.flow-library-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    cursor: pointer;
    transition: background 0.1s;
}

.flow-library-item:hover {
    background: var(--bg-tertiary);
}

.flow-library-item.selected {
    background: var(--bg-tertiary);
    border-left: 2px solid var(--accent);
}

.flow-library-preview {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.flow-library-preview-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
}

.flow-library-preview-header h2 {
    margin: 0;
    font-size: 18px;
}

.start-run-btn {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 500;
    padding: 8px 20px;
}

.start-run-btn:hover {
    opacity: 0.9;
}

.flow-library-graph {
    flex: 1;
    position: relative;
}

.flow-library-no-selection {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-secondary);
}
```

#### URL state management

Use `useSearchParams` from `react-router-dom` to persist the selected flow in the URL query parameter `?flow=<id>`. This allows:
- Sidebar links to navigate to a specific flow: `navigate('/?flow=code_review')`
- Browser back/forward to restore selection
- Sharing URLs

#### Error banner integration

The `ErrorBanner` component (UI-013) is rendered above the graph preview when the selected flow has errors. The graph still renders using the flow's node/edge data (even if invalid, the backend may return partial parse results or the last valid state).

#### Flow list auto-update

The `useFlowWatcher` hook (UI-014) provides a reactive `flows` array that updates when the backend pushes `flow.file_changed`/`flow.file_error`/`flow.file_valid` events. The Flow Library re-renders with updated validity indicators without manual refresh.

#### Fork/join edge mapping

When converting `FlowEdgeDef` to `GraphView` edges, fork edges (one source, multiple targets) need to be expanded:

```typescript
function expandEdges(edgeDefs: FlowEdgeDef[]): Array<{ source: string; target: string; edgeType: EdgeType; condition?: string }> {
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

### Edge Cases
- No flows discovered — show "No flows discovered" message with hint about the watched directory
- All flows are invalid — list renders with hollow dots, graph preview shows error banner
- Selected flow is deleted from disk — flow disappears from list; if it was selected, clear selection
- Selected flow becomes invalid after a file change — error banner appears, graph may update
- Very large flow (many nodes) — React Flow's `fitView` auto-zooms to fit the graph
- Backend is not running — API calls fail; show a connection error message

## Testing Strategy
Minimal for MVP:
1. Page renders without crashing with empty flow list
2. Page renders flow items when given data
3. Selecting a flow shows the graph preview
4. Visual verification: layout looks correct, "Start Run" button appears for valid flows
