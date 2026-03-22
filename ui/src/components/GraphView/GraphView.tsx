import { useMemo, useCallback, useEffect, useRef } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Controls,
  Background,
  useReactFlow,
  type Node,
  type Edge,
  type NodeChange,
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
  taskDirs?: Map<string, string>;
  taskCwds?: Map<string, string>;
  worktreePath?: string;
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
  taskDirs?: Map<string, string>,
  taskCwds?: Map<string, string>,
  worktreePath?: string,
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

// --- Inner component (must be inside ReactFlowProvider) ---

function GraphViewInner({
  nodes,
  edges,
  taskStatuses,
  taskGenerations,
  taskElapsed,
  taskDirs,
  taskCwds,
  worktreePath,
  activeEdges,
  traversedEdges,
  readOnly = false,
  onNodeClick,
  waitUntil,
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

  // Re-fit when node dimensions change (e.g., node expanded/collapsed on click)
  const handleNodesChange = useCallback(
    (changes: NodeChange<Node<NodePillData>>[]) => {
      if (changes.some((c) => c.type === 'dimensions')) {
        scheduleFitView(100);
      }
    },
    [scheduleFitView],
  );

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

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (fitTimerRef.current) clearTimeout(fitTimerRef.current);
    };
  }, []);

  return (
    <div ref={containerRef} className="graph-view">
      <ReactFlow
        nodes={layouted.nodes}
        edges={layouted.edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        onNodesChange={handleNodesChange}
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
