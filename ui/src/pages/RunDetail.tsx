import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useFlowRun } from '../hooks/useFlowRun';
import { GraphView } from '../components/GraphView';
import { LogViewer } from '../components/LogViewer';
import { ControlPanel } from '../components/ControlPanel';
import { expandEdges } from '../utils/edges';
import { api } from '../api/client';
import type {
  TaskStatus,
  FlowRunDetail as FlowRunDetailType,
} from '../api/types';
import './RunDetail.css';

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const { run, tasks, selectedTask, selectTask, logs, isConnected, send } =
    useFlowRun(id!);

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

  // Get logs for the selected task
  const selectedTaskExecution = selectedTask
    ? tasks.get(selectedTask)
    : undefined;
  const selectedLogs = selectedTaskExecution
    ? (logs.get(selectedTaskExecution.id) ?? [])
    : [];

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
        <h1>
          {run.flow_name} <span className="run-id">#{run.id.slice(0, 8)}</span>
        </h1>
        <span className={`run-status status-${run.status}`}>{run.status}</span>
        {!isConnected && (
          <span className="ws-disconnected">Reconnecting...</span>
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
            activeEdges={activeEdges}
            waitUntil={waitUntil}
            selectedNode={selectedTask}
            onNodeClick={(nodeName) => selectTask(nodeName)}
          />
        </div>

        <div className="run-detail-logs">
          <LogViewer logs={selectedLogs} taskName={selectedTask} />
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
