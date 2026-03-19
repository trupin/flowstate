import { useMemo, useCallback } from 'react';
import {
  ReactFlow,
  Controls,
  Background,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import type { FlowNodeDef, FlowEdgeDef, TaskStatus } from '../../api/types';
import './GraphView.css';

// --- Props ---

export interface GraphViewProps {
  nodes: FlowNodeDef[];
  edges: FlowEdgeDef[];
  taskStatuses?: Map<string, TaskStatus>;
  activeEdges?: Set<string>;
  readOnly?: boolean;
  selectedNode?: string | null;
  onNodeClick?: (nodeName: string) => void;
  waitUntil?: Map<string, string>;
}

// --- Custom node data type ---

interface FlowNodeData {
  label: string;
  nodeType: string;
  status: TaskStatus;
  waitUntil?: string;
  [key: string]: unknown;
}

// --- Layout ---

function getLayoutedElements(
  nodes: Node<FlowNodeData>[],
  edges: Edge[],
): { nodes: Node<FlowNodeData>[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 60, ranksep: 80 });

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
): Node<FlowNodeData>[] {
  return nodeDefs.map((n) => ({
    id: n.name,
    type: 'flowNode',
    data: {
      label: n.name,
      nodeType: n.type,
      status: statuses?.get(n.name) ?? 'pending',
      waitUntil: waitUntilMap?.get(n.name),
    },
    position: { x: 0, y: 0 },
  }));
}

function convertToReactFlowEdges(
  edgeDefs: FlowEdgeDef[],
  activeEdges?: Set<string>,
): Edge[] {
  return edgeDefs.map((e, i) => {
    const source = e.source ?? '';
    const target = e.target ?? '';
    const id = `${source}-${target}-${i}`;
    const isActive = activeEdges?.has(id) ?? false;
    return {
      id,
      source,
      target,
      label: e.condition ? truncate(e.condition, 30) : undefined,
      style: {
        strokeDasharray: e.edge_type === 'conditional' ? '5 5' : undefined,
        stroke: isActive ? 'var(--accent)' : 'var(--text-secondary)',
        strokeWidth: isActive ? 2 : 1,
      },
      animated: isActive,
      markerEnd: { type: 'arrowclosed' as const },
    };
  });
}

// --- Custom node component ---

const STATUS_COLORS: Record<TaskStatus, string> = {
  pending: 'var(--status-pending)',
  waiting: 'var(--status-waiting)',
  running: 'var(--status-running)',
  completed: 'var(--status-completed)',
  failed: 'var(--status-failed)',
  skipped: 'var(--status-skipped)',
};

function getCountdown(isoTarget: string | undefined): string | null {
  if (!isoTarget) return null;
  const remaining = new Date(isoTarget).getTime() - Date.now();
  if (remaining <= 0) return '0s';
  const secs = Math.ceil(remaining / 1000);
  if (secs >= 60) {
    const mins = Math.floor(secs / 60);
    return `${mins}m ${secs % 60}s`;
  }
  return `${secs}s`;
}

function FlowNodeComponent({ data }: NodeProps<Node<FlowNodeData>>) {
  const status = data.status;
  const bgColor = STATUS_COLORS[status] ?? 'var(--status-pending)';
  const countdown = status === 'waiting' ? getCountdown(data.waitUntil) : null;

  return (
    <div
      className={`flow-node status-${status}`}
      style={{ backgroundColor: bgColor }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="flow-node-handle"
      />
      <span className="flow-node-label">{data.label}</span>
      {countdown !== null && (
        <span className="flow-node-countdown">{countdown}</span>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="flow-node-handle"
      />
    </div>
  );
}

const nodeTypes = {
  flowNode: FlowNodeComponent,
};

// --- Main component ---

export function GraphView({
  nodes,
  edges,
  taskStatuses,
  activeEdges,
  readOnly = false,
  onNodeClick,
  waitUntil,
}: GraphViewProps) {
  const rfNodes = useMemo(
    () => convertToReactFlowNodes(nodes, taskStatuses, waitUntil),
    [nodes, taskStatuses, waitUntil],
  );
  const rfEdges = useMemo(
    () => convertToReactFlowEdges(edges, activeEdges),
    [edges, activeEdges],
  );
  const layouted = useMemo(
    () => getLayoutedElements(rfNodes, rfEdges),
    [rfNodes, rfEdges],
  );

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node<FlowNodeData>) => {
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
