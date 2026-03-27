import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Controls,
  Background,
  useReactFlow,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import type { FlowNodeDef, FlowEdgeDef, TaskStatus } from '../../api/types';
import { NodePill, type NodePillData } from '../NodePill';
import { ConditionalEdge } from './ConditionalEdge';
import './GraphView.css';

// --- Props ---

export interface GraphViewProps {
  nodes: FlowNodeDef[];
  edges: FlowEdgeDef[];
  taskStatuses?: Map<string, TaskStatus>;
  taskGenerations?: Map<string, number>;
  taskElapsed?: Map<string, number>;
  taskDirs?: Map<string, string>;
  taskCwds?: Map<string, string>;
  taskExecutionIds?: Map<string, string>;
  worktreePath?: string;
  activeEdges?: Set<string>;
  traversedEdges?: Set<string>;
  readOnly?: boolean;
  selectedNode?: string | null;
  onNodeClick?: (nodeName: string) => void;
  waitUntil?: Map<string, string>;
  runId?: string;
  subtaskVersion?: number;
}

// --- Layout ---

const DEFAULT_NODE_WIDTH = 150;
const DEFAULT_NODE_HEIGHT = 40;

/** Run dagre layout on nodes/edges using default dimensions. */
function runDagreLayout(
  nodes: Node<NodePillData>[],
  edges: Edge[],
): { nodes: Node<NodePillData>[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 80, ranksep: 100 });

  nodes.forEach((node) => {
    const width = node.measured?.width ?? DEFAULT_NODE_WIDTH;
    const height = node.measured?.height ?? DEFAULT_NODE_HEIGHT;
    g.setNode(node.id, { width, height });
  });

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    const width = node.measured?.width ?? DEFAULT_NODE_WIDTH;
    const height = node.measured?.height ?? DEFAULT_NODE_HEIGHT;
    return {
      ...node,
      position: {
        x: (pos?.x ?? 0) - width / 2,
        y: (pos?.y ?? 0) - height / 2,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
}

// --- Conversion helpers ---

function convertToReactFlowNodes(
  nodeDefs: FlowNodeDef[],
  statuses?: Map<string, TaskStatus>,
  waitUntilMap?: Map<string, string>,
  generations?: Map<string, number>,
  elapsed?: Map<string, number>,
  taskDirs?: Map<string, string>,
  taskCwds?: Map<string, string>,
  worktreePath?: string,
  selectedNode?: string | null,
  taskExecutionIds?: Map<string, string>,
  runId?: string,
  subtaskVersion?: number,
): Node<NodePillData>[] {
  return nodeDefs.map((n) => {
    const hasExecution = statuses?.has(n.name) ?? false;
    // Use runtime cwd from the task execution if available, fall back to the
    // flow definition's static cwd
    const runtimeCwd = taskCwds?.get(n.name);
    return {
      id: n.name,
      type: 'flowNode',
      data: {
        label: n.name,
        nodeType: n.type,
        status: statuses?.get(n.name) ?? 'pending',
        waitUntil: waitUntilMap?.get(n.name),
        generation: generations?.get(n.name),
        elapsedSeconds: elapsed?.get(n.name),
        cwd: runtimeCwd ?? n.cwd,
        taskDir: taskDirs?.get(n.name),
        worktreeDir: worktreePath,
        hasExecution,
        isSelected: selectedNode === n.name,
        runId,
        taskExecutionId: taskExecutionIds?.get(n.name),
        subtaskVersion,
      },
      position: { x: 0, y: 0 },
    };
  });
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

    const useConditionalEdge =
      e.edge_type === 'conditional' && !isBackEdge && e.condition;

    return {
      id,
      source,
      target,
      type: useConditionalEdge ? 'conditional' : 'smoothstep',
      ...(useConditionalEdge
        ? { data: { condition: e.condition, stroke, isActive, isTraversed } }
        : {}),
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

const edgeTypes = {
  conditional: ConditionalEdge,
};

// --- Inner component (must be inside ReactFlowProvider) ---

function GraphViewInner({
  nodes,
  edges,
  taskStatuses,
  taskGenerations,
  taskElapsed,
  taskDirs,
  taskCwds,
  taskExecutionIds,
  worktreePath,
  activeEdges,
  traversedEdges,
  readOnly = false,
  selectedNode,
  onNodeClick,
  waitUntil,
  runId,
  subtaskVersion,
}: GraphViewProps) {
  const { fitView } = useReactFlow();
  const containerRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef(false);

  const rfNodes = useMemo(
    () =>
      convertToReactFlowNodes(
        nodes,
        taskStatuses,
        waitUntil,
        taskGenerations,
        taskElapsed,
        taskDirs,
        taskCwds,
        worktreePath,
        selectedNode,
        taskExecutionIds,
        runId,
        subtaskVersion,
      ),
    [
      nodes,
      taskStatuses,
      waitUntil,
      taskGenerations,
      taskElapsed,
      taskDirs,
      taskCwds,
      worktreePath,
      selectedNode,
      taskExecutionIds,
      runId,
      subtaskVersion,
    ],
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
  // State for dagre-positioned nodes and edges
  const [layoutedNodes, setLayoutedNodes] = useState<Node<NodePillData>[]>([]);
  const [layoutedEdges, setLayoutedEdges] = useState<Edge[]>([]);

  // Re-run dagre when input nodes/edges change (new data from props)
  useEffect(() => {
    const result = runDagreLayout(rfNodes, rfEdges);
    setLayoutedNodes(result.nodes);
    setLayoutedEdges(result.edges);
  }, [rfNodes, rfEdges]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node<NodePillData>) => {
      onNodeClick?.(node.id);
    },
    [onNodeClick],
  );

  // Debounced fitView helper — waits for React Flow to re-measure nodes
  const fitTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const scheduleFitView = useCallback(
    (delay = 100) => {
      if (fitTimerRef.current) clearTimeout(fitTimerRef.current);
      fitTimerRef.current = setTimeout(() => {
        const duration = mountedRef.current ? 200 : 0;
        fitView({ duration, padding: 0.15 });
      }, delay);
    },
    [fitView],
  );

  // Re-fit when container size changes (e.g., detail panel opens/closes)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const observer = new ResizeObserver(() => {
      // Delay to let React Flow re-measure node dimensions after layout shift
      scheduleFitView(150);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [scheduleFitView]);

  // Fit view when nodes count changes (new nodes added or removed)
  useEffect(() => {
    if (!mountedRef.current) {
      const timer = setTimeout(() => {
        mountedRef.current = true;
      }, 300);
      return () => clearTimeout(timer);
    }
    scheduleFitView(100);
  }, [nodes.length, scheduleFitView]);

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      if (fitTimerRef.current) clearTimeout(fitTimerRef.current);
    };
  }, []);

  return (
    <div ref={containerRef} className="graph-view">
      <ReactFlow
        nodes={layoutedNodes}
        edges={layoutedEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={handleNodeClick}
        nodesDraggable={!readOnly}
        nodesConnectable={false}
        elementsSelectable={!readOnly}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} />
        <Background color="var(--border)" gap={20} />
      </ReactFlow>
    </div>
  );
}

// --- Main component (public) ---

export function GraphView(props: GraphViewProps) {
  if (props.nodes.length === 0) {
    return (
      <div className="graph-view graph-view-empty">
        <span>No nodes</span>
      </div>
    );
  }

  return (
    <ReactFlowProvider>
      <GraphViewInner {...props} />
    </ReactFlowProvider>
  );
}
