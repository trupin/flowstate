# [UI-005] Node Component (compact pills + expandable)

## Domain
ui

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: UI-004
- Blocks: UI-011

## Spec References
- specs.md Section 10.4 — "Graph Visualization" (node design, colors, generation badge)
- agents/05-ui.md — "Node rendering — hybrid compact/expanded"

## Summary
Create the custom React Flow node component that renders flow nodes as compact pills by default, with click-to-expand metadata and hover tooltips. This replaces the placeholder node rendering from UI-004 with the full hybrid compact/expanded design specified in specs.md Section 10.4.

## Acceptance Criteria
- [ ] All node elements include `data-testid="node-{name}"` and `data-status="{status}"` attributes (required for E2E tests)
- [ ] `ui/src/components/NodePill.tsx` exists and is registered as a custom React Flow node type
- [ ] `ui/src/components/NodePill.css` exists with node-specific styles
- [ ] Default state: compact pill showing node name + status color fill
- [ ] Compact pill is small enough that large graphs (10+ nodes) remain readable
- [ ] Click on a node expands it in-place to show: type badge (entry/task/exit), generation count, elapsed time, cwd
- [ ] Expansion does NOT disrupt the overall graph layout (other nodes don't move)
- [ ] Second click on an expanded node collapses it back to compact
- [ ] Hover tooltip shows quick info: status, type, generation — without expanding
- [ ] Generation badge: shows "xN" when generation > 1 (e.g., "x3" for third execution of a cycle node)
- [ ] Entry nodes have a double border visual distinction
- [ ] Exit nodes have a thick border visual distinction
- [ ] Status colors are applied via CSS variables (`--status-pending`, `--status-running`, etc.)
- [ ] Running nodes have the pulse animation from UI-004 CSS
- [ ] NodePill is integrated with GraphView as a registered `nodeTypes` entry

## Technical Design

### Files to Create/Modify
- `ui/src/components/NodePill.tsx` — custom React Flow node component
- `ui/src/components/NodePill.css` — node styling
- `ui/src/components/GraphView.tsx` — register `NodePill` as a custom node type

### Key Implementation Details

#### Custom node data interface

```typescript
interface NodePillData {
    label: string;                   // node name
    nodeType: 'entry' | 'task' | 'exit';
    status: TaskStatus;              // pending | running | completed | failed | skipped | paused | waiting
    generation?: number;             // cycle re-entry count (default 1)
    elapsedSeconds?: number;         // time spent executing
    cwd?: string;                    // working directory
    waitUntil?: string;              // ISO timestamp for waiting nodes
}
```

#### Component structure

```typescript
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useState } from 'react';
import './NodePill.css';

export function NodePill({ data, selected }: NodeProps<NodePillData>) {
    const [expanded, setExpanded] = useState(false);

    const statusClass = `status-${data.status}`;
    const typeClass = `type-${data.nodeType}`;

    return (
        <div
            className={`node-pill ${statusClass} ${typeClass} ${expanded ? 'expanded' : ''}`}
            onClick={(e) => {
                e.stopPropagation();
                setExpanded(!expanded);
            }}
            title={!expanded ? `${data.label} (${data.status})` : undefined}
        >
            <Handle type="target" position={Position.Top} />

            {/* Compact view (always visible) */}
            <div className="node-pill-compact">
                <span className="node-pill-name">{data.label}</span>
                {(data.generation ?? 1) > 1 && (
                    <span className="node-pill-generation">x{data.generation}</span>
                )}
            </div>

            {/* Expanded view (visible when expanded) */}
            {expanded && (
                <div className="node-pill-details">
                    <span className="node-pill-type-badge">{data.nodeType}</span>
                    {data.elapsedSeconds != null && (
                        <span className="node-pill-elapsed">{formatElapsed(data.elapsedSeconds)}</span>
                    )}
                    {data.cwd && (
                        <span className="node-pill-cwd" title={data.cwd}>
                            {truncatePath(data.cwd)}
                        </span>
                    )}
                    {data.status === 'waiting' && data.waitUntil && (
                        <span className="node-pill-countdown">
                            <CountdownTimer until={data.waitUntil} />
                        </span>
                    )}
                </div>
            )}

            <Handle type="source" position={Position.Bottom} />
        </div>
    );
}
```

#### Registration in GraphView

```typescript
// In GraphView.tsx
import { NodePill } from './NodePill';

const nodeTypes = {
    flowNode: NodePill,
};

// Pass to ReactFlow:
<ReactFlow nodeTypes={nodeTypes} ... />
```

#### CSS styling

```css
.node-pill {
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s ease;
    min-width: 80px;
    text-align: center;
    border: 2px solid transparent;
}

/* Status color fills */
.node-pill.status-pending   { background: var(--status-pending); color: #fff; }
.node-pill.status-waiting   { background: var(--status-waiting); color: #fff; }
.node-pill.status-running   { background: var(--status-running); color: #fff; }
.node-pill.status-completed { background: var(--status-completed); color: #fff; }
.node-pill.status-failed    { background: var(--status-failed); color: #fff; }
.node-pill.status-skipped   { background: var(--status-skipped); color: #fff; }
.node-pill.status-paused    { background: var(--status-paused); color: #000; }

/* Entry node: double border */
.node-pill.type-entry {
    border: 2px double rgba(255, 255, 255, 0.6);
}

/* Exit node: thick border */
.node-pill.type-exit {
    border: 3px solid rgba(255, 255, 255, 0.6);
}

/* Expanded state */
.node-pill.expanded {
    border-radius: 12px;
    padding: 8px 14px;
}

/* Generation badge */
.node-pill-generation {
    font-size: 10px;
    margin-left: 4px;
    opacity: 0.8;
    background: rgba(0, 0, 0, 0.2);
    padding: 1px 4px;
    border-radius: 8px;
}

/* Details section */
.node-pill-details {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 4px;
    font-size: 10px;
    opacity: 0.9;
}

.node-pill-type-badge {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 1px;
    opacity: 0.7;
}

.node-pill-cwd {
    font-family: var(--font-mono);
    font-size: 9px;
    opacity: 0.7;
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
```

#### Hover tooltip

Use the native `title` attribute for simplicity (set on the outer div when NOT expanded). Shows: "node_name (status)". For MVP, this is sufficient. A custom tooltip component can be added later if needed.

#### Countdown timer for waiting nodes

A small internal component or utility:

```typescript
function CountdownTimer({ until }: { until: string }) {
    const [remaining, setRemaining] = useState('');

    useEffect(() => {
        const interval = setInterval(() => {
            const diff = new Date(until).getTime() - Date.now();
            if (diff <= 0) {
                setRemaining('ready');
                clearInterval(interval);
            } else {
                const secs = Math.floor(diff / 1000);
                const mins = Math.floor(secs / 60);
                setRemaining(mins > 0 ? `${mins}m ${secs % 60}s` : `${secs}s`);
            }
        }, 1000);
        return () => clearInterval(interval);
    }, [until]);

    return <span>{remaining}</span>;
}
```

#### Helper functions

```typescript
function formatElapsed(seconds: number): string {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${mins}m ${secs}s`;
}

function truncatePath(path: string, maxLen = 20): string {
    if (path.length <= maxLen) return path;
    return '...' + path.slice(-maxLen + 3);
}
```

### Edge Cases
- Node name is very long (30+ chars) — truncate with ellipsis in compact mode, show full in expanded
- Generation is 1 — do NOT show the badge (only show when > 1)
- Elapsed time is undefined (task hasn't started) — don't show elapsed time row
- cwd is undefined — don't show cwd row
- Waiting node with a `waitUntil` in the past — show "ready" instead of negative countdown
- Expanding a node should NOT trigger the `onNodeClick` callback in GraphView for selecting it as the log target — the click event must be carefully handled. `onNodeClick` from React Flow fires separately; use `stopPropagation` on the expand toggle if needed, or handle both in a single click handler.
- React Flow requires `Handle` components for edge connections — include both target (top) and source (bottom) handles

## Testing Strategy
Minimal for MVP:
1. NodePill renders without crashing with minimal data (name + status)
2. NodePill renders generation badge when generation > 1
3. Visual verification: pill colors match the status, entry/exit have distinct borders
4. Visual verification: click toggles expansion, hover shows tooltip
