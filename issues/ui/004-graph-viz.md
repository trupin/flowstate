# [UI-004] Graph Visualization (React Flow + dagre)

## Domain
ui

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-002
- Blocks: UI-005, UI-010, UI-011

## Spec References
- specs.md Section 10.4 — "Graph Visualization"
- agents/05-ui.md — "Graph Visualization (`GraphView.tsx`)"

## Summary
Create the graph visualization component using React Flow v12+ (`@xyflow/react`) with dagre for automatic top-to-bottom layout. This component renders a flow's nodes and edges with status-dependent colors and animations. It supports two modes: read-only (for Flow Library preview) and interactive (for Run Detail with live status updates). The graph is the centerpiece of the UI — it occupies ~60% of the Run Detail page.

## Acceptance Criteria
- [ ] `ui/src/components/GraphView.tsx` exists and renders a React Flow graph
- [ ] `ui/src/components/GraphView.css` exists with graph-specific styles
- [ ] Graph uses dagre for layout with `rankdir: 'TB'` (top-to-bottom)
- [ ] Layout is recalculated when graph structure changes (nodes added/removed)
- [ ] Nodes render as compact pills by default (name + status color fill)
- [ ] Node colors match specs.md Section 10.4: Pending (#9CA3AF), Waiting (#A855F7), Running (#3B82F6 + pulse), Completed (#22C55E), Failed (#EF4444), Skipped (#F97316), Paused (#EAB308)
- [ ] Running nodes have an animated pulse CSS effect
- [ ] Waiting nodes display a countdown timer
- [ ] Edges render correctly: solid (unconditional), dashed (conditional with label), solid diverging (fork), solid converging (join)
- [ ] Active edges (currently being traversed) are highlighted/animated
- [ ] Read-only mode disables node dragging, selection, and interaction
- [ ] Interactive mode allows clicking nodes (emits an `onNodeClick` callback)
- [ ] Currently running node has a glow/enlarged effect
- [ ] Component accepts flow definition (nodes + edges) and optional task status map as props
- [ ] React Flow controls (zoom, fit view) are available

## Technical Design

### Files to Create/Modify
- `ui/src/components/GraphView.tsx` — main graph component
- `ui/src/components/GraphView.css` — graph-specific styles (node colors, animations, edge styles)

### Key Implementation Details

#### Props interface

```typescript
interface GraphViewProps {
    nodes: FlowNodeDef[];           // node definitions from the flow AST
    edges: FlowEdgeDef[];           // edge definitions from the flow AST
    taskStatuses?: Map<string, TaskStatus>;  // node_name → current status (for live runs)
    activeEdges?: Set<string>;      // edge IDs currently being traversed
    readOnly?: boolean;             // true for Flow Library preview
    selectedNode?: string | null;   // currently selected node name
    onNodeClick?: (nodeName: string) => void;
    waitUntil?: Map<string, string>; // node_name → ISO timestamp for waiting nodes
}

interface FlowNodeDef {
    name: string;
    type: 'entry' | 'task' | 'exit';
}

interface FlowEdgeDef {
    source: string;
    target: string;
    edgeType: 'unconditional' | 'conditional' | 'fork' | 'join';
    condition?: string;             // truncated when-clause text for conditional edges
}
```

#### dagre layout

```typescript
import dagre from 'dagre';

function getLayoutedElements(nodes: Node[], edges: Edge[]) {
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: 'TB', nodesep: 60, ranksep: 80 });

    nodes.forEach(node => {
        g.setNode(node.id, { width: 150, height: 40 });
    });

    edges.forEach(edge => {
        g.setEdge(edge.source, edge.target);
    });

    dagre.layout(g);

    const layoutedNodes = nodes.map(node => {
        const pos = g.node(node.id);
        return { ...node, position: { x: pos.x - 75, y: pos.y - 20 } };
    });

    return { nodes: layoutedNodes, edges };
}
```

Call `getLayoutedElements` whenever the node/edge structure changes. Memoize with `useMemo` to avoid re-layout on every render.

#### React Flow setup

```typescript
import { ReactFlow, Controls, Background, type Node, type Edge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

export function GraphView({ nodes, edges, taskStatuses, readOnly, onNodeClick, ... }: GraphViewProps) {
    const rfNodes = useMemo(() => convertToReactFlowNodes(nodes, taskStatuses), [nodes, taskStatuses]);
    const rfEdges = useMemo(() => convertToReactFlowEdges(edges, activeEdges), [edges, activeEdges]);
    const { nodes: layoutedNodes, edges: layoutedEdges } = useMemo(
        () => getLayoutedElements(rfNodes, rfEdges),
        [rfNodes, rfEdges]
    );

    return (
        <div className="graph-view">
            <ReactFlow
                nodes={layoutedNodes}
                edges={layoutedEdges}
                nodeTypes={nodeTypes}
                onNodeClick={(_, node) => onNodeClick?.(node.id)}
                nodesDraggable={!readOnly}
                nodesConnectable={false}
                elementsSelectable={!readOnly}
                fitView
            >
                <Controls />
                <Background color="var(--border)" gap={20} />
            </ReactFlow>
        </div>
    );
}
```

Register a custom node type (`nodeTypes`) that renders the compact pill. This custom node component will be implemented in UI-005 (`NodePill.tsx`). For this issue, use a simple default rendering that shows the node name with a status-colored background. UI-005 will replace it with the full pill component.

#### Node conversion

Map flow definition nodes to React Flow nodes:

```typescript
function convertToReactFlowNodes(nodeDefs: FlowNodeDef[], statuses?: Map<string, TaskStatus>): Node[] {
    return nodeDefs.map(n => ({
        id: n.name,
        type: 'flowNode',  // custom node type
        data: {
            label: n.name,
            nodeType: n.type,
            status: statuses?.get(n.name) ?? 'pending',
        },
        position: { x: 0, y: 0 },  // will be set by dagre
    }));
}
```

#### Edge conversion

Map flow definition edges to React Flow edges:

```typescript
function convertToReactFlowEdges(edgeDefs: FlowEdgeDef[], activeEdges?: Set<string>): Edge[] {
    return edgeDefs.map((e, i) => {
        const id = `${e.source}-${e.target}-${i}`;
        const isActive = activeEdges?.has(id) ?? false;
        return {
            id,
            source: e.source,
            target: e.target,
            label: e.condition ? truncate(e.condition, 30) : undefined,
            style: {
                strokeDasharray: e.edgeType === 'conditional' ? '5 5' : undefined,
                stroke: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                strokeWidth: isActive ? 2 : 1,
            },
            animated: isActive,
            markerEnd: { type: 'arrowclosed' },
        };
    });
}
```

#### CSS animations

```css
/* Pulse animation for running nodes */
@keyframes pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
    50% { box-shadow: 0 0 0 8px rgba(59, 130, 246, 0); }
}

.flow-node.status-running {
    animation: pulse 2s ease-in-out infinite;
}

/* Glow effect for the currently running node */
.flow-node.status-running {
    box-shadow: 0 0 12px rgba(59, 130, 246, 0.5);
    transform: scale(1.05);
}
```

#### Fork/join edge rendering

Fork edges (one source → multiple targets) and join edges (multiple sources → one target) use normal solid arrows. The visual "grouping" comes naturally from dagre layout — multiple arrows diverging from or converging to a node are visually apparent. No special edge component needed.

### Edge Cases
- Empty flow (no nodes) — show an empty graph with a "No nodes" message
- Single node flow — dagre handles single nodes fine; just center it
- Very large graphs (20+ nodes) — dagre layout works but may need fitView to auto-zoom
- Node names with long text — truncate in the pill, full name in tooltip (UI-005)
- Rapid status updates — React Flow handles re-renders efficiently; ensure `useMemo` prevents unnecessary dagre re-layout when only statuses change (not structure)
- Fork edges where source has multiple targets — create one React Flow edge per target

## Testing Strategy
Minimal for MVP:
1. Component renders without crashing with an empty node/edge list
2. Component renders without crashing with a simple 3-node graph (entry → task → exit)
3. Visual verification: nodes appear in top-to-bottom layout, edges connect correctly
4. Visual verification: different statuses show different colors
