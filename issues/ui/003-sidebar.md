# [UI-003] Sidebar Component (Flows, Active Runs, Schedules)

## Domain
ui

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-002, UI-009, UI-014
- Blocks: UI-010

## Spec References
- specs.md Section 10.7 — "Sidebar"
- agents/05-ui.md — "Sidebar (`Sidebar.tsx`)" and "Layout"

## Summary
Create the persistent left sidebar that provides navigation across all sections of the Flowstate UI. The sidebar has three collapsible sections: FLOWS (discovered `.flow` files with validity indicators), ACTIVE RUNS (currently running/paused flows with status colors), and SCHEDULES (recurring flows with next trigger time). The sidebar subscribes to WebSocket events for real-time updates to flow validity and run status.

## Acceptance Criteria
- [ ] Flow entries include `data-testid="sidebar-flow-{name}"` with `data-status="valid"|"error"`, run entries include `data-testid="sidebar-run-{id}"` (required for E2E tests)
- [ ] `ui/src/components/Sidebar.tsx` exists and renders three collapsible sections
- [ ] `ui/src/components/Sidebar.css` exists with sidebar-specific styles
- [ ] FLOWS section lists all discovered flows from `GET /api/flows`
- [ ] Each flow shows a green filled dot (valid) or hollow dot (errors) next to its name
- [ ] Clicking a flow navigates to the Flow Library page (`/`) with that flow selected (via URL param or state)
- [ ] ACTIVE RUNS section lists running/paused flows from `GET /api/runs?status=running` (and paused)
- [ ] Each active run shows a colored status indicator matching its current status
- [ ] Clicking an active run navigates to `/runs/:id`
- [ ] SCHEDULES section lists schedules from `GET /api/schedules`
- [ ] Each schedule shows the flow name and next trigger time (human-readable)
- [ ] Each section is collapsible (click header to toggle)
- [ ] Sidebar is fixed-width (`var(--sidebar-width)`, 240px) and full-height
- [ ] Sidebar updates in real-time via WebSocket events (flow validity changes, run status changes)

## Technical Design

### Files to Create/Modify
- `ui/src/components/Sidebar.tsx` — the sidebar component
- `ui/src/components/Sidebar.css` — sidebar styles
- `ui/src/App.tsx` — integrate Sidebar into the app layout (render it alongside routes)

### Key Implementation Details

#### Component Structure

```typescript
// Sidebar.tsx
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { DiscoveredFlow, FlowRun, FlowSchedule } from '../api/types';
import './Sidebar.css';

export function Sidebar() {
    const navigate = useNavigate();
    const [flows, setFlows] = useState<DiscoveredFlow[]>([]);
    const [activeRuns, setActiveRuns] = useState<FlowRun[]>([]);
    const [schedules, setSchedules] = useState<FlowSchedule[]>([]);
    const [collapsed, setCollapsed] = useState({
        flows: false,
        runs: false,
        schedules: false,
    });

    // Fetch initial data on mount
    // Subscribe to WebSocket for real-time updates (via useFlowWatcher for flows)

    return (
        <aside className="sidebar">
            <div className="sidebar-brand">FLOWSTATE</div>

            {/* FLOWS section */}
            <SidebarSection title="FLOWS" collapsed={collapsed.flows}
                onToggle={() => setCollapsed(s => ({...s, flows: !s.flows}))}>
                {flows.map(flow => (
                    <div key={flow.id} className="sidebar-item"
                        onClick={() => navigate(`/?flow=${flow.id}`)}>
                        <span className={`validity-dot ${flow.is_valid ? 'valid' : 'invalid'}`} />
                        <span className="sidebar-item-name">{flow.name}</span>
                    </div>
                ))}
            </SidebarSection>

            {/* ACTIVE RUNS section */}
            <SidebarSection title="ACTIVE RUNS" collapsed={collapsed.runs}
                onToggle={() => setCollapsed(s => ({...s, runs: !s.runs}))}>
                {activeRuns.map(run => (
                    <div key={run.id} className="sidebar-item"
                        onClick={() => navigate(`/runs/${run.id}`)}>
                        <span className={`status-dot status-${run.status}`} />
                        <span className="sidebar-item-name">{run.flow_name} #{run.id.slice(0, 4)}</span>
                    </div>
                ))}
            </SidebarSection>

            {/* SCHEDULES section */}
            <SidebarSection title="SCHEDULES" collapsed={collapsed.schedules}
                onToggle={() => setCollapsed(s => ({...s, schedules: !s.schedules}))}>
                {schedules.map(sched => (
                    <div key={sched.id} className="sidebar-item">
                        <span className="sidebar-item-name">{sched.flow_name}</span>
                        <span className="sidebar-item-meta">next: {formatNextTrigger(sched.next_trigger_at)}</span>
                    </div>
                ))}
            </SidebarSection>
        </aside>
    );
}
```

#### `SidebarSection` sub-component

A small internal component (can be in the same file) that renders a collapsible section with a header and children:

```typescript
function SidebarSection({ title, collapsed, onToggle, children }) {
    return (
        <div className="sidebar-section">
            <div className="sidebar-section-header" onClick={onToggle}>
                <span className={`collapse-arrow ${collapsed ? 'collapsed' : ''}`}>▶</span>
                <span>{title}</span>
            </div>
            {!collapsed && <div className="sidebar-section-content">{children}</div>}
        </div>
    );
}
```

#### CSS Layout

```css
.sidebar {
    width: var(--sidebar-width);
    height: 100vh;
    position: fixed;
    left: 0;
    top: 0;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    z-index: 10;
}

.sidebar-brand {
    padding: 16px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 2px;
    color: var(--text-secondary);
    border-bottom: 1px solid var(--border);
}
```

#### Validity dots

- Green filled circle for valid flows: `background: var(--success); border-radius: 50%;`
- Hollow circle for invalid flows: `border: 1.5px solid var(--text-secondary); background: transparent; border-radius: 50%;`

#### Status dots for active runs

Use the `--status-*` CSS variables. The status class (`status-running`, `status-paused`, etc.) sets the `background-color` to the corresponding variable.

#### Real-time updates

The Sidebar needs live data. Two approaches (choose based on what hooks are available):

1. **For flows**: Use the `useFlowWatcher` hook (UI-014) which listens for `flow.file_changed`, `flow.file_error`, `flow.file_valid` WebSocket events and re-fetches `GET /api/flows`.
2. **For active runs**: Listen for `flow.started`, `flow.status_changed`, `flow.completed` WebSocket events. On any of these, re-fetch `GET /api/runs?status=running`.
3. **For schedules**: Fetch on mount. Schedules change infrequently; no real-time subscription needed for MVP.

#### App layout integration

In `App.tsx`, render the Sidebar alongside the router outlet:

```typescript
function App() {
    return (
        <BrowserRouter>
            <div style={{ display: 'flex' }}>
                <Sidebar />
                <main style={{ marginLeft: 'var(--sidebar-width)', flex: 1 }}>
                    <Routes>
                        <Route path="/" element={<FlowLibrary />} />
                        <Route path="/runs/:id" element={<RunDetail />} />
                    </Routes>
                </main>
            </div>
        </BrowserRouter>
    );
}
```

### Edge Cases
- No flows discovered yet (empty `flows_dir`) — show "No flows found" message in FLOWS section
- No active runs — show "No active runs" in ACTIVE RUNS section
- No schedules — show "No schedules" in SCHEDULES section
- Long flow names — truncate with ellipsis (`text-overflow: ellipsis; overflow: hidden; white-space: nowrap`)
- API errors on initial fetch — silently retry or show a subtle error indicator
- Very many flows/runs — the sidebar scrolls independently (it has `overflow-y: auto`)

## Testing Strategy
Minimal for MVP:
1. Component renders without crashing when given empty arrays
2. Component renders items when given mock data
3. Visual verification: sidebar is visible, sections collapse/expand, dots have correct colors
