import { useState, useEffect, useCallback } from 'react';
import { useWebSocket } from './useWebSocket';
import { api } from '../api/client';
import type {
  FlowRun,
  TaskExecution,
  EdgeTransition,
  LogEntry,
  FlowEvent,
} from '../api/types';

interface UseFlowRunReturn {
  run: FlowRun | null;
  tasks: Map<string, TaskExecution>;
  edges: EdgeTransition[];
  selectedTask: string | null;
  selectTask: (nodeName: string | null) => void;
  logs: Map<string, LogEntry[]>;
  isConnected: boolean;
  send: (data: unknown) => void;
}

function applyEvent(
  event: FlowEvent,
  setRun: React.Dispatch<React.SetStateAction<FlowRun | null>>,
  setTasks: React.Dispatch<React.SetStateAction<Map<string, TaskExecution>>>,
  setEdges: React.Dispatch<React.SetStateAction<EdgeTransition[]>>,
  setLogs: React.Dispatch<React.SetStateAction<Map<string, LogEntry[]>>>,
) {
  const { type, payload } = event;
  switch (type) {
    case 'flow.status_changed':
      setRun((prev) =>
        prev
          ? { ...prev, status: payload.new_status as FlowRun['status'] }
          : prev,
      );
      break;
    case 'flow.completed':
      setRun((prev) =>
        prev
          ? {
              ...prev,
              status: payload.final_status as FlowRun['status'],
              elapsed_seconds: payload.elapsed_seconds as number,
            }
          : prev,
      );
      break;
    case 'flow.budget_warning':
      setRun((prev) =>
        prev
          ? {
              ...prev,
              elapsed_seconds: payload.elapsed_seconds as number,
            }
          : prev,
      );
      break;
    case 'task.started':
      setTasks((prev) => {
        const next = new Map(prev);
        next.set(payload.node_name as string, {
          ...next.get(payload.node_name as string),
          id: payload.task_execution_id as string,
          flow_run_id: event.flow_run_id,
          node_name: payload.node_name as string,
          node_type:
            (payload.node_type as TaskExecution['node_type']) ?? 'task',
          status: 'running',
          generation: payload.generation as number,
          context_mode: (payload.context_mode as string) ?? 'full_history',
          cwd: (payload.cwd as string) ?? '.',
        });
        return next;
      });
      break;
    case 'task.completed':
      setTasks((prev) => {
        const next = new Map(prev);
        const existing = next.get(payload.node_name as string);
        if (existing) {
          next.set(payload.node_name as string, {
            ...existing,
            status: 'completed',
            elapsed_seconds: payload.elapsed_seconds as number,
          });
        }
        return next;
      });
      break;
    case 'task.failed':
      setTasks((prev) => {
        const next = new Map(prev);
        const existing = next.get(payload.node_name as string);
        if (existing) {
          next.set(payload.node_name as string, {
            ...existing,
            status: 'failed',
            error_message: payload.error_message as string,
          });
        }
        return next;
      });
      break;
    case 'task.log':
      setLogs((prev) => {
        const next = new Map(prev);
        const taskExecId = payload.task_execution_id as string;
        const taskLogs = next.get(taskExecId) ?? [];
        next.set(taskExecId, [
          ...taskLogs,
          {
            id: taskLogs.length,
            task_execution_id: taskExecId,
            content: payload.content as string,
            log_type: payload.log_type as LogEntry['log_type'],
            timestamp: event.timestamp,
          },
        ]);
        return next;
      });
      break;
    case 'task.waiting':
      setTasks((prev) => {
        const next = new Map(prev);
        const existing = next.get(payload.node_name as string);
        if (existing) {
          next.set(payload.node_name as string, {
            ...existing,
            status: 'waiting',
            wait_until: payload.wait_until as string,
          });
        }
        return next;
      });
      break;
    case 'edge.transition':
      setEdges((prev) => [
        ...prev,
        {
          id: `${String(payload.from_node)}-${String(payload.to_node)}-${Date.now()}`,
          flow_run_id: event.flow_run_id,
          from_node: payload.from_node as string,
          to_node: payload.to_node as string,
          edge_type:
            (payload.edge_type as EdgeTransition['edge_type']) ??
            'unconditional',
          condition: payload.condition as string | undefined,
          judge_reasoning: payload.judge_reasoning as string | undefined,
          judge_confidence: payload.judge_confidence as number | undefined,
          created_at: event.timestamp,
        },
      ]);
      break;
  }
}

export function useFlowRun(runId: string): UseFlowRunReturn {
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
  const ws = useWebSocket(wsUrl);
  const [run, setRun] = useState<FlowRun | null>(null);
  const [tasks, setTasks] = useState<Map<string, TaskExecution>>(new Map());
  const [edges, setEdges] = useState<EdgeTransition[]>([]);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [logs, setLogs] = useState<Map<string, LogEntry[]>>(new Map());

  // Initial fetch
  useEffect(() => {
    api.runs.get(runId).then((detail) => {
      setRun(detail);
      const taskMap = new Map<string, TaskExecution>();
      detail.tasks.forEach((t) => taskMap.set(t.node_name, t));
      setTasks(taskMap);
      setEdges(detail.edges);
    });
  }, [runId]);

  // Subscribe to WebSocket
  useEffect(() => {
    ws.subscribe(runId);
    return () => ws.unsubscribe(runId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  // Process incoming events
  useEffect(() => {
    if (!ws.lastEvent || ws.lastEvent.flow_run_id !== runId) return;
    applyEvent(ws.lastEvent, setRun, setTasks, setEdges, setLogs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws.lastEvent]);

  // Fetch task logs from the API when a task is selected and we have no logs yet.
  // This handles the case where the run completed before the page loaded, so
  // WebSocket log events were never received.
  useEffect(() => {
    if (!selectedTask || !run) return;
    const taskExec = tasks.get(selectedTask);
    if (!taskExec) return;

    // Only fetch if we don't already have logs for this task
    const existingLogs = logs.get(taskExec.id);
    if (existingLogs && existingLogs.length > 0) return;

    api.runs
      .taskLogs(runId, taskExec.id)
      .then((resp) => {
        // The API returns { logs: [...], ... } but client types it as LogEntry[]
        const logEntries: LogEntry[] = Array.isArray(resp)
          ? resp
          : ((resp as unknown as { logs: LogEntry[] }).logs ?? []);
        if (logEntries.length > 0) {
          setLogs((prev) => {
            const next = new Map(prev);
            next.set(taskExec.id, logEntries);
            return next;
          });
        }
      })
      .catch(() => {
        // Silently ignore fetch errors
      });
  }, [selectedTask, tasks, run, runId, logs]);

  const selectTask = useCallback((nodeName: string | null) => {
    setSelectedTask(nodeName);
  }, []);

  return {
    run,
    tasks,
    edges,
    selectedTask,
    selectTask,
    logs,
    isConnected: ws.isConnected,
    send: ws.send,
  };
}
