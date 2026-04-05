import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
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
  allTaskExecutions: Map<string, TaskExecution[]>;
  edges: EdgeTransition[];
  selectedTask: string | null;
  selectTask: (nodeName: string | null) => void;
  clearManualSelection: () => void;
  autoSelectedTask: string | null;
  isManualSelection: boolean;
  runningTaskNames: string[];
  logs: Map<string, LogEntry[]>;
  clearLogs: (taskExecutionId: string) => void;
  isConnected: boolean;
  send: (data: unknown) => void;
  subtaskVersion: number;
}

function upsertAllTaskExecutions(
  setAllTaskExecutions: React.Dispatch<
    React.SetStateAction<Map<string, TaskExecution[]>>
  >,
  nodeName: string,
  taskExec: TaskExecution,
) {
  setAllTaskExecutions((prev) => {
    const next = new Map(prev);
    const list = [...(next.get(nodeName) ?? [])];
    const existingIdx = list.findIndex((e) => e.id === taskExec.id);
    if (existingIdx >= 0) {
      list[existingIdx] = taskExec;
    } else {
      list.push(taskExec);
    }
    next.set(nodeName, list);
    return next;
  });
}

function applyEvent(
  event: FlowEvent,
  setRun: React.Dispatch<React.SetStateAction<FlowRun | null>>,
  setTasks: React.Dispatch<React.SetStateAction<Map<string, TaskExecution>>>,
  setAllTaskExecutions: React.Dispatch<
    React.SetStateAction<Map<string, TaskExecution[]>>
  >,
  setEdges: React.Dispatch<React.SetStateAction<EdgeTransition[]>>,
  setLogs: React.Dispatch<React.SetStateAction<Map<string, LogEntry[]>>>,
  setRunningTaskNames: React.Dispatch<React.SetStateAction<string[]>>,
  fetchRunDetail: () => void,
) {
  const { type, payload } = event;
  switch (type) {
    case 'flow.status_changed':
      setRun((prev) =>
        prev
          ? {
              ...prev,
              status: payload.new_status as FlowRun['status'],
              error_message:
                (payload.error_message as string | undefined) ??
                prev.error_message,
            }
          : prev,
      );
      // Re-fetch on terminal status changes to update task statuses
      if (
        ['completed', 'failed', 'cancelled'].includes(
          payload.new_status as string,
        )
      ) {
        fetchRunDetail();
      }
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
      // Re-fetch full run detail to update task statuses in the graph
      fetchRunDetail();
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
    case 'task.started': {
      const startedExec: TaskExecution = {
        id: payload.task_execution_id as string,
        flow_run_id: event.flow_run_id,
        node_name: payload.node_name as string,
        node_type: (payload.node_type as TaskExecution['node_type']) ?? 'task',
        status: 'running',
        generation: payload.generation as number,
        context_mode: (payload.context_mode as string) ?? 'full_history',
        cwd: (payload.cwd as string) ?? '.',
        task_dir: (payload.task_dir as string) ?? undefined,
      };
      setTasks((prev) => {
        const next = new Map(prev);
        next.set(payload.node_name as string, {
          ...next.get(payload.node_name as string),
          ...startedExec,
        });
        return next;
      });
      upsertAllTaskExecutions(
        setAllTaskExecutions,
        payload.node_name as string,
        startedExec,
      );
      setRunningTaskNames((prev) => {
        const name = payload.node_name as string;
        if (prev.includes(name)) return prev;
        return [...prev, name].sort();
      });
      break;
    }
    case 'task.completed': {
      const completedNodeName = payload.node_name as string;
      setTasks((prev) => {
        const next = new Map(prev);
        const existing = next.get(completedNodeName);
        next.set(completedNodeName, {
          id: existing?.id ?? (payload.task_execution_id as string) ?? '',
          flow_run_id: existing?.flow_run_id ?? event.flow_run_id,
          node_name: completedNodeName,
          node_type: existing?.node_type ?? 'task',
          status: 'completed',
          generation: existing?.generation ?? 1,
          context_mode: existing?.context_mode ?? 'full_history',
          cwd: existing?.cwd ?? '.',
          elapsed_seconds: payload.elapsed_seconds as number,
        });
        return next;
      });
      setAllTaskExecutions((prev) => {
        const next = new Map(prev);
        const list = [...(next.get(completedNodeName) ?? [])];
        const execId = (payload.task_execution_id as string | undefined) ?? '';
        const idx = list.findIndex((e) => e.id === execId);
        if (idx >= 0 && list[idx]) {
          list[idx] = {
            ...list[idx],
            status: 'completed',
            elapsed_seconds: payload.elapsed_seconds as number,
          };
          next.set(completedNodeName, list);
        }
        return next;
      });
      setRunningTaskNames((prev) => {
        const name = completedNodeName;
        const filtered = prev.filter((n) => n !== name);
        return filtered.length === prev.length ? prev : filtered;
      });
      break;
    }
    case 'task.failed': {
      const failedNodeName = payload.node_name as string;
      setTasks((prev) => {
        const next = new Map(prev);
        const existing = next.get(failedNodeName);
        next.set(failedNodeName, {
          id: existing?.id ?? (payload.task_execution_id as string) ?? '',
          flow_run_id: existing?.flow_run_id ?? event.flow_run_id,
          node_name: failedNodeName,
          node_type: existing?.node_type ?? 'task',
          status: 'failed',
          generation: existing?.generation ?? 1,
          context_mode: existing?.context_mode ?? 'full_history',
          cwd: existing?.cwd ?? '.',
          error_message: payload.error_message as string,
        });
        return next;
      });
      setAllTaskExecutions((prev) => {
        const next = new Map(prev);
        const list = [...(next.get(failedNodeName) ?? [])];
        const execId = (payload.task_execution_id as string | undefined) ?? '';
        const idx = list.findIndex((e) => e.id === execId);
        if (idx >= 0 && list[idx]) {
          list[idx] = {
            ...list[idx],
            status: 'failed',
            error_message: payload.error_message as string,
          };
          next.set(failedNodeName, list);
        }
        return next;
      });
      setRunningTaskNames((prev) => {
        const name = failedNodeName;
        const filtered = prev.filter((n) => n !== name);
        return filtered.length === prev.length ? prev : filtered;
      });
      break;
    }
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
    case 'task.waiting': {
      const waitingNodeName = payload.node_name as string;
      setTasks((prev) => {
        const next = new Map(prev);
        const existing = next.get(waitingNodeName);
        next.set(waitingNodeName, {
          id: existing?.id ?? (payload.task_execution_id as string) ?? '',
          flow_run_id: existing?.flow_run_id ?? event.flow_run_id,
          node_name: waitingNodeName,
          node_type: existing?.node_type ?? 'task',
          status: 'waiting',
          generation: existing?.generation ?? 1,
          context_mode: existing?.context_mode ?? 'full_history',
          cwd: existing?.cwd ?? '.',
          wait_until: payload.wait_until as string,
        });
        return next;
      });
      setAllTaskExecutions((prev) => {
        const next = new Map(prev);
        const list = [...(next.get(waitingNodeName) ?? [])];
        const execId = (payload.task_execution_id as string | undefined) ?? '';
        const idx = list.findIndex((e) => e.id === execId);
        if (idx >= 0 && list[idx]) {
          list[idx] = {
            ...list[idx],
            status: 'waiting',
            wait_until: payload.wait_until as string,
          };
          next.set(waitingNodeName, list);
        }
        return next;
      });
      break;
    }
    case 'task.interrupted': {
      const interruptedNodeName = payload.node_name as string;
      setTasks((prev) => {
        const next = new Map(prev);
        const existing = next.get(interruptedNodeName);
        if (existing) {
          next.set(interruptedNodeName, { ...existing, status: 'interrupted' });
        }
        return next;
      });
      setAllTaskExecutions((prev) => {
        const next = new Map(prev);
        const list = [...(next.get(interruptedNodeName) ?? [])];
        const execId = (payload.task_execution_id as string | undefined) ?? '';
        const idx = list.findIndex((e) => e.id === execId);
        if (idx >= 0 && list[idx]) {
          list[idx] = { ...list[idx], status: 'interrupted' };
          next.set(interruptedNodeName, list);
        }
        return next;
      });
      setRunningTaskNames((prev) => {
        const name = interruptedNodeName;
        const filtered = prev.filter((n) => n !== name);
        return filtered.length === prev.length ? prev : filtered;
      });
      break;
    }
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
  const { send, subscribe, unsubscribe, eventQueue, clearQueue, isConnected } =
    useWebSocket(wsUrl);
  const [run, setRun] = useState<FlowRun | null>(null);
  const [tasks, setTasks] = useState<Map<string, TaskExecution>>(new Map());
  const [allTaskExecutions, setAllTaskExecutions] = useState<
    Map<string, TaskExecution[]>
  >(new Map());
  const [edges, setEdges] = useState<EdgeTransition[]>([]);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [isManualSelection, setIsManualSelection] = useState(false);
  const [runningTaskNames, setRunningTaskNames] = useState<string[]>([]);
  const [lastAutoSelectedTask, setLastAutoSelectedTask] = useState<
    string | null
  >(null);
  const [logs, setLogs] = useState<Map<string, LogEntry[]>>(new Map());
  const [subtaskVersion, setSubtaskVersion] = useState(0);

  // Fallback timer ref: when a cancel/abort action is sent, we start a 3-second
  // timeout. If the flow status hasn't become terminal by then, we re-fetch from
  // the REST API to ensure the UI reflects the actual state. This handles the
  // case where the WebSocket status_changed event is lost or never emitted.
  const cancelFallbackTimer = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );

  // Clear fallback timer when status becomes terminal
  useEffect(() => {
    if (
      run &&
      ['completed', 'failed', 'cancelled', 'budget_exceeded'].includes(
        run.status,
      )
    ) {
      if (cancelFallbackTimer.current !== null) {
        clearTimeout(cancelFallbackTimer.current);
        cancelFallbackTimer.current = null;
      }
    }
  }, [run]);

  // Cleanup fallback timer on unmount
  useEffect(() => {
    return () => {
      if (cancelFallbackTimer.current !== null) {
        clearTimeout(cancelFallbackTimer.current);
      }
    };
  }, []);

  // Auto-select the first running task (alphabetically) when not manually selecting
  const autoSelectedTask: string | null = useMemo(() => {
    if (isManualSelection) return null;
    return runningTaskNames[0] ?? lastAutoSelectedTask;
  }, [isManualSelection, runningTaskNames, lastAutoSelectedTask]);

  // Track the last auto-selected task so it persists after all tasks complete
  useEffect(() => {
    if (autoSelectedTask) {
      setLastAutoSelectedTask(autoSelectedTask);
    }
  }, [autoSelectedTask]);

  // Fetch run detail from the API and update state
  const fetchRunDetail = useCallback(() => {
    api.runs
      .get(runId)
      .then((detail) => {
        setRun(detail);
        const taskMap = new Map<string, TaskExecution>();
        const allExecsMap = new Map<string, TaskExecution[]>();
        detail.tasks.forEach((t) => {
          // Keep latest for existing consumers
          const existing = taskMap.get(t.node_name);
          if (!existing || t.generation > existing.generation) {
            taskMap.set(t.node_name, t);
          }
          // Collect all executions
          const list = allExecsMap.get(t.node_name) ?? [];
          list.push(t);
          allExecsMap.set(t.node_name, list);
        });
        // Sort each node's executions by started_at ascending
        allExecsMap.forEach((execs) =>
          execs.sort((a, b) =>
            (a.started_at ?? '').localeCompare(b.started_at ?? ''),
          ),
        );
        setTasks(taskMap);
        setAllTaskExecutions(allExecsMap);
        setEdges(detail.edges);
        // Sync running task names from fetched data
        const running = detail.tasks
          .filter((t) => t.status === 'running')
          .map((t) => t.node_name)
          .sort();
        setRunningTaskNames(running);
      })
      .catch(() => {
        // Silently ignore fetch errors (e.g. network issues during reconnect)
      });
  }, [runId]);

  // Initial fetch
  useEffect(() => {
    fetchRunDetail();
  }, [fetchRunDetail]);

  // Re-fetch when WebSocket reconnects (to catch events missed during disconnect)
  const prevConnected = useRef(false);
  useEffect(() => {
    if (isConnected && !prevConnected.current) {
      // Just reconnected — re-fetch to sync state
      fetchRunDetail();
    }
    prevConnected.current = isConnected;
  }, [isConnected, fetchRunDetail]);

  // Subscribe to WebSocket
  useEffect(() => {
    subscribe(runId);
    return () => unsubscribe(runId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  // Process incoming events from the queue
  useEffect(() => {
    if (eventQueue.length === 0) return;
    const count = eventQueue.length;
    let hasSubtaskEvent = false;
    for (const event of eventQueue) {
      if (event.flow_run_id !== runId) continue;
      if (event.type === 'subtask.updated') {
        hasSubtaskEvent = true;
      }
      applyEvent(
        event,
        setRun,
        setTasks,
        setAllTaskExecutions,
        setEdges,
        setLogs,
        setRunningTaskNames,
        fetchRunDetail,
      );
    }
    if (hasSubtaskEvent) {
      setSubtaskVersion((v) => v + 1);
    }
    clearQueue(count);
  }, [eventQueue, clearQueue, runId, fetchRunDetail]);

  // The effective task is whichever task the user should be viewing:
  // manual selection takes priority, otherwise auto-selected.
  const effectiveTask = selectedTask ?? autoSelectedTask;

  // Fetch task logs from the API when a task is selected (manually or auto)
  // and we have no logs yet. This handles the case where the run completed
  // before the page loaded, so WebSocket log events were never received.
  // Fetches logs for ALL executions of the selected node so that switching
  // between runs in the execution picker has data immediately.
  useEffect(() => {
    if (!effectiveTask || !run) return;
    const nodeExecs = allTaskExecutions.get(effectiveTask) ?? [];
    if (nodeExecs.length === 0) return;

    for (const taskExec of nodeExecs) {
      // Only fetch if we don't already have logs for this execution
      const existingLogs = logs.get(taskExec.id);
      if (existingLogs && existingLogs.length > 0) continue;

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
    }
  }, [effectiveTask, allTaskExecutions, run, runId, logs]);

  const selectTask = useCallback((nodeName: string | null) => {
    if (nodeName === null) {
      // Deselecting: resume auto-follow
      setSelectedTask(null);
      setIsManualSelection(false);
    } else {
      // Manual selection: override auto-follow
      setSelectedTask(nodeName);
      setIsManualSelection(true);
    }
  }, []);

  const clearManualSelection = useCallback(() => {
    setSelectedTask(null);
    setIsManualSelection(false);
  }, []);

  const clearLogs = useCallback((taskExecutionId: string) => {
    setLogs((prev) => {
      const next = new Map(prev);
      next.set(taskExecutionId, []);
      return next;
    });
  }, []);

  // Wrap send to detect cancel/abort actions and start a fallback timer.
  // If the backend status_changed event doesn't arrive within 3 seconds,
  // re-fetch from the REST API to ensure the UI reflects cancellation.
  const wrappedSend = useCallback(
    (data: unknown) => {
      send(data);
      const msg = data as Record<string, unknown> | null;
      if (
        msg &&
        typeof msg === 'object' &&
        (msg.action === 'cancel' || msg.action === 'abort')
      ) {
        // Clear any existing timer before starting a new one
        if (cancelFallbackTimer.current !== null) {
          clearTimeout(cancelFallbackTimer.current);
        }
        cancelFallbackTimer.current = setTimeout(() => {
          cancelFallbackTimer.current = null;
          fetchRunDetail();
        }, 3000);
      }
    },
    [send, fetchRunDetail],
  );

  return {
    run,
    tasks,
    allTaskExecutions,
    edges,
    selectedTask,
    selectTask,
    clearManualSelection,
    autoSelectedTask,
    isManualSelection,
    runningTaskNames,
    logs,
    clearLogs,
    isConnected,
    send: wrappedSend,
    subtaskVersion,
  };
}
