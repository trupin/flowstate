import { useMemo, useCallback } from 'react';
import {
  ReactFlow,
  Controls,
  Background,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import type { FlowNodeDef, FlowEdgeDef, TaskStatus } from '../../api/types';
import { NodePill, type NodePillData } from '../NodePill';
import './GraphView.css';

// --- Props ---

export interface GraphViewProps {
  nodes: FlowNodeDef[];
  edges: FlowEdgeDef[];
  taskStatuses?: Map<string, TaskStatus>;
  taskGenerations?: Map<string, number>;
  taskElapsed?: Map<string, number>;
  activeEdges?: Set<string>;
  traversedEdges?: Set<string>;
  readOnly?: boolean;
  selectedNode?: string | null;
  onNodeClick?: (nodeName: string) => void;
  waitUntil?: Map<string, string>;
}

// --- Layout ---

function getLayoutedElements(
  nodes: Node<NodePillData>[],
  edges: Edge[],
): { nodes: Node<NodePillData>[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 80, ranksep: 100 });

  nodes.forEach((node) => {
    g.setNode(node.id, { width: 150, height: 40 });
  });

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: { x: (pos?.x ?? 0) - 75, y: (pos?.y ?? 0) - 20 },
    };
  });

  return { nodes: layoutedNodes, edges };
}

// --- Conversion helpers ---

function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen - 1) + '\u2026';
}

function convertToReactFlowNodes(
  nodeDefs: FlowNodeDef[],
  statuses?: Map<string, TaskStatus>,
  waitUntilMap?: Map<string, string>,
  generations?: Map<string, number>,
  elapsed?: Map<string, number>,
): Node<NodePillData>[] {
  return nodeDefs.map((n) => ({
    id: n.name,
    type: 'flowNode',
    data: {
      label: n.name,
      nodeType: n.type,
      status: statuses?.get(n.name) ?? 'pending',
      waitUntil: waitUntilMap?.get(n.name),
      generation: generations?.get(n.name),
      elapsedSeconds: elapsed?.get(n.name),
      cwd: n.cwd,
    },
    position: { x: 0, y: 0 },
  }));
}

function convertToReactFlowEdges(
  edgeDefs: FlowEdgeDef[],
  nodeOrder: Map<string, number>,
  activeEdges?: Set<string>,
  traversedEdges?: Set<string>,
): Edge[] {
  return edgeDefs.map((e, i) => {
    const source = e.source ?? '';
    const target = e.target ?? '';
    const id = `${source}-${target}-${i}`;
    const isActive = activeEdges?.has(id) ?? false;
    const isTraversed = (traversedEdges?.has(id) ?? false) && !isActive;
    const sourceRank = nodeOrder.get(source) ?? 0;
    const targetRank = nodeOrder.get(target) ?? 0;
    const isBackEdge = targetRank <= sourceRank;

    // Determine stroke color: active (blue) > traversed (green) > back-edge (accent) > idle (gray)
    let stroke = 'var(--text-secondary)';
    if (isActive) {
      stroke = 'var(--accent)';
    } else if (isTraversed) {
      stroke = 'var(--success)';
    } else if (isBackEdge) {
      stroke = 'var(--accent)';
    }

    return {
      id,
      source,
      target,
      type: 'smoothstep',
      label: isBackEdge
        ? undefined
        : e.condition
          ? truncate(e.condition, 40)
          : undefined,
      labelBgPadding: [6, 4] as [number, number],
      labelBgBorderRadius: 4,
      labelBgStyle: { fill: 'var(--bg-secondary)', fillOpacity: 0.95 },
      labelStyle: { fill: 'var(--text-primary)', fontSize: 11 },
      style: {
        strokeDasharray: e.edge_type === 'conditional' ? '5 5' : undefined,
        stroke,
        strokeWidth: isActive || isTraversed || isBackEdge ? 2 : 1,
        opacity: isBackEdge && !isActive && !isTraversed ? 0.6 : 1,
      },
      animated: isActive,
      markerEnd: { type: 'arrowclosed' as const },
    };
  });
}

// --- Registered custom node types ---

const nodeTypes = {
  flowNode: NodePill,
};

// --- Main component ---

export function GraphView({
  nodes,
  edges,
  taskStatuses,
  taskGenerations,
  taskElapsed,
  activeEdges,
  traversedEdges,
  readOnly = false,
  onNodeClick,
  waitUntil,
}: GraphViewProps) {
  const rfNodes = useMemo(
    () =>
      convertToReactFlowNodes(
        nodes,
        taskStatuses,
        waitUntil,
        taskGenerations,
        taskElapsed,
      ),
    [nodes, taskStatuses, waitUntil, taskGenerations, taskElapsed],
  );
  const nodeOrder = useMemo(() => {
    const order = new Map<string, number>();
    nodes.forEach((n, i) => order.set(n.name, i));
    return order;
  }, [nodes]);
  const rfEdges = useMemo(
    () =>
      convertToReactFlowEdges(edges, nodeOrder, activeEdges, traversedEdges),
    [edges, nodeOrder, activeEdges, traversedEdges],
  );
  const layouted = useMemo(
    () => getLayoutedElements(rfNodes, rfEdges),
    [rfNodes, rfEdges],
  );

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node<NodePillData>) => {
      onNodeClick?.(node.id);
    },
    [onNodeClick],
  );

  if (nodes.length === 0) {
    return (
      <div className="graph-view graph-view-empty">
        <span>No nodes</span>
      </div>
    );
  }

  return (
    <div className="graph-view">
      <ReactFlow
        nodes={layouted.nodes}
        edges={layouted.edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        nodesDraggable={!readOnly}
        nodesConnectable={false}
        elementsSelectable={!readOnly}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Controls />
        <Background color="var(--border)" gap={20} />
      </ReactFlow>
    </div>
  );
}
