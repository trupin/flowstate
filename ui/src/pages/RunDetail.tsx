import { useMemo, useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useFlowRun } from '../hooks/useFlowRun';
import { GraphView } from '../components/GraphView';
import { LogViewer } from '../components/LogViewer';
import type { TaskExecutionInfo } from '../components/LogViewer';
import { ControlPanel } from '../components/ControlPanel';
import { OrchestratorConsole } from '../components/OrchestratorConsole';
import { expandEdges } from '../utils/edges';
import { api } from '../api/client';
import type {
  TaskStatus,
  OrchestratorInfo,
  FlowRunDetail as FlowRunDetailType,
} from '../api/types';
import './RunDetail.css';

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const {
    run,
    tasks,
    edges,
    selectedTask,
    selectTask,
    autoSelectedTask,
    isManualSelection,
    logs,
    isConnected,
    send,
  } = useFlowRun(id!);

  // The effective task shown in log viewer and highlighted in graph:
  // manual selection takes priority, then auto-follow.
  const effectiveTask = selectedTask ?? autoSelectedTask;
  const isAutoFollow = !isManualSelection && effectiveTask !== null;

  const [showOrchestrator, setShowOrchestrator] = useState(false);
  const [orchestrators, setOrchestrators] = useState<OrchestratorInfo[]>([]);

  // Fetch orchestrators once the run is available
  const runLoaded = run !== null;
  useEffect(() => {
    if (!runLoaded) return;
    api.runs.orchestrators(id!).then((result) => {
      setOrchestrators(result);
    });
  }, [runLoaded, id]);

  // Build task status map for the graph
  const taskStatuses = useMemo(() => {
    const map = new Map<string, TaskStatus>();
    tasks.forEach((task, nodeName) => map.set(nodeName, task.status));
    return map;
  }, [tasks]);

  // Build task generation map for the graph
  const taskGenerations = useMemo(() => {
    const map = new Map<string, number>();
    tasks.forEach((task, nodeName) => map.set(nodeName, task.generation));
    return map;
  }, [tasks]);

  // Build task elapsed map for the graph
  const taskElapsed = useMemo(() => {
    const map = new Map<string, number>();
    tasks.forEach((task, nodeName) => {
      if (task.elapsed_seconds != null) {
        map.set(nodeName, task.elapsed_seconds);
      }
    });
    return map;
  }, [tasks]);

  // Build task directory maps for the graph
  const taskDirs = useMemo(() => {
    const map = new Map<string, string>();
    tasks.forEach((task, nodeName) => {
      if (task.task_dir) {
        map.set(nodeName, task.task_dir);
      }
    });
    return map;
  }, [tasks]);

  const taskCwds = useMemo(() => {
    const map = new Map<string, string>();
    tasks.forEach((task, nodeName) => {
      if (task.cwd) {
        map.set(nodeName, task.cwd);
      }
    });
    return map;
  }, [tasks]);

  // Get logs for the effective task (manual or auto-selected)
  const selectedTaskExecution = effectiveTask
    ? tasks.get(effectiveTask)
    : undefined;
  const selectedLogs = selectedTaskExecution
    ? (logs.get(selectedTaskExecution.id) ?? [])
    : [];

  // Build task execution info for the log viewer details panel
  const taskExecutionInfo: TaskExecutionInfo | null = useMemo(() => {
    if (!selectedTaskExecution) return null;
    return {
      nodeType: selectedTaskExecution.node_type,
      elapsedSeconds: selectedTaskExecution.elapsed_seconds ?? null,
      cwd: selectedTaskExecution.cwd || null,
      taskDir: selectedTaskExecution.task_dir ?? null,
      worktreeDir: (run as FlowRunDetailType | null)?.worktree_path ?? null,
      status: selectedTaskExecution.status,
      waitUntil: selectedTaskExecution.wait_until ?? null,
    };
  }, [selectedTaskExecution, run]);

  // Graph node/edge definitions from the flow
  const detail = run as FlowRunDetailType | null;

  const graphNodes = useMemo(() => {
    if (!detail?.flow) return [];
    return detail.flow.nodes.map((n) => ({
      name: n.name,
      type: n.type,
      prompt: n.prompt,
      cwd: n.cwd,
    }));
  }, [detail?.flow]);

  const graphEdges = useMemo(() => {
    if (!detail?.flow) return [];
    return expandEdges(detail.flow.edges);
  }, [detail?.flow]);

  // Active edges (currently being traversed)
  const activeEdges = useMemo(() => {
    const active = new Set<string>();
    tasks.forEach((task, nodeName) => {
      if (task.status === 'running') {
        graphEdges.forEach((e, i) => {
          if (e.target === nodeName) {
            active.add(`${e.source}-${e.target}-${i}`);
          }
        });
      }
    });
    return active;
  }, [tasks, graphEdges]);

  // Traversed edges (transitions that have already completed)
  const traversedEdges = useMemo(() => {
    const traversed = new Set<string>();
    for (const edge of edges) {
      graphEdges.forEach((e, i) => {
        if (e.source === edge.from_node && e.target === edge.to_node) {
          traversed.add(`${e.source}-${e.target}-${i}`);
        }
      });
    }
    return traversed;
  }, [edges, graphEdges]);

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
    send({
      action: 'pause',
      flow_run_id: id,
      payload: {},
    });
  }

  function handleResume() {
    void api.runs.resume(id!);
  }

  function handleCancel() {
    send({
      action: 'cancel',
      flow_run_id: id,
      payload: {},
    });
  }

  function handleRetry(taskId: string) {
    send({
      action: 'retry_task',
      flow_run_id: id,
      payload: { task_execution_id: taskId },
    });
  }

  function handleSkip(taskId: string) {
    send({
      action: 'skip_task',
      flow_run_id: id,
      payload: { task_execution_id: taskId },
    });
  }

  if (!run) {
    return <div className="run-detail-loading">Loading run...</div>;
  }

  return (
    <div className="run-detail">
      <div className="run-detail-header">
        <div className="run-detail-header-top">
          <h1>
            {run.flow_name}{' '}
            <span className="run-id">#{run.id.slice(0, 8)}</span>
          </h1>
          <span className={`run-status status-${run.status}`}>
            {run.status}
          </span>
          {orchestrators.length > 0 && (
            <button
              className={`orchestrator-toggle-btn ${showOrchestrator ? 'active' : ''}`}
              onClick={() => setShowOrchestrator((prev) => !prev)}
            >
              Orchestrator
            </button>
          )}
          {!isConnected && (
            <span className="ws-disconnected">Reconnecting...</span>
          )}
        </div>
        {run.error_message && (
          <div className="run-error-banner">
            <span className="run-error-label">Error</span>
            <span className="run-error-message">{run.error_message}</span>
          </div>
        )}
        {selectedTaskExecution?.error_message && (
          <div className="task-error-banner">
            <span className="task-error-label">
              {selectedTaskExecution.node_name} failed
            </span>
            <span className="task-error-message">
              {selectedTaskExecution.error_message}
            </span>
          </div>
        )}
      </div>

      <div className="run-detail-main">
        <div className="run-detail-graph">
          <GraphView
            nodes={graphNodes}
            edges={graphEdges}
            taskStatuses={taskStatuses}
            taskGenerations={taskGenerations}
            taskElapsed={taskElapsed}
            taskDirs={taskDirs}
            taskCwds={taskCwds}
            worktreePath={detail?.worktree_path}
            activeEdges={activeEdges}
            traversedEdges={traversedEdges}
            waitUntil={waitUntil}
            selectedNode={effectiveTask}
            onNodeClick={(nodeName) =>
              selectTask(nodeName === effectiveTask ? null : nodeName)
            }
          />
        </div>

        <div className="run-detail-logs">
          {showOrchestrator ? (
            <OrchestratorConsole
              runId={id!}
              isActive={run.status === 'running' || run.status === 'paused'}
            />
          ) : (
            <LogViewer
              logs={selectedLogs}
              taskName={effectiveTask}
              taskExecution={taskExecutionInfo}
              isAutoFollow={isAutoFollow}
              runId={id}
              taskExecutionId={selectedTaskExecution?.id}
            />
          )}
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
