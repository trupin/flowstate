import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useWebSocket } from './useWebSocket';
import { api } from '../api/client';
import type {
  ControlActionType,
  FlowRun,
  TaskExecution,
  EdgeTransition,
  LogEntry,
  FlowEvent,
} from '../api/types';

// Pending-action state: tracks a control action (cancel, retry_task, etc.)
// that was sent over the websocket and is waiting for an ack, error, or
// downstream flow event. A ``task_execution_id`` is present for task-level
// actions so we can match incoming acks/errors precisely.
export interface PendingAction {
  action: ControlActionType;
  task_execution_id?: string;
  started_at: number;
}

export interface ActionErrorState {
  action?: ControlActionType;
  task_execution_id?: string;
  message: string;
}

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
  pendingAction: PendingAction | null;
  actionError: ActionErrorState | null;
  dismissActionError: () => void;
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
    case 'task.retried':
    case 'task.skipped':
      // Re-fetch so the new task execution and any resulting run-status
      // transition appear atomically.
      fetchRunDetail();
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
  const {
    send,
    subscribe,
    unsubscribe,
    eventQueue,
    clearQueue,
    controlQueue,
    clearControlQueue,
    isConnected,
  } = useWebSocket(wsUrl);
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
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(
    null,
  );
  const [actionError, setActionError] = useState<ActionErrorState | null>(null);

  // When a control action is sent, we start a 3s timer. If no ack, error, or
  // matching event arrives by then, re-fetch from REST to unblock the UI.
  const pendingFallbackTimer = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );

  // Synchronous mirror so timers and drain effects can read current pending
  // without stale closures.
  const pendingActionRef = useRef<PendingAction | null>(null);
  useEffect(() => {
    pendingActionRef.current = pendingAction;
  }, [pendingAction]);

  const clearPending = useCallback(() => {
    if (pendingFallbackTimer.current !== null) {
      clearTimeout(pendingFallbackTimer.current);
      pendingFallbackTimer.current = null;
    }
    setPendingAction(null);
  }, []);

  // Clear fallback timer + pending state when status becomes terminal.
  // A terminal status means any outstanding cancel/abort/pause has settled.
  useEffect(() => {
    if (
      run &&
      ['completed', 'failed', 'cancelled', 'budget_exceeded'].includes(
        run.status,
      )
    ) {
      if (pendingFallbackTimer.current !== null) {
        clearTimeout(pendingFallbackTimer.current);
        pendingFallbackTimer.current = null;
      }
      const currentPending = pendingActionRef.current;
      if (
        currentPending &&
        (currentPending.action === 'cancel' ||
          currentPending.action === 'abort' ||
          currentPending.action === 'pause')
      ) {
        setPendingAction(null);
      }
    }
  }, [run]);

  // Cleanup fallback timer on unmount
  useEffect(() => {
    return () => {
      if (pendingFallbackTimer.current !== null) {
        clearTimeout(pendingFallbackTimer.current);
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
      // Clear pending retry/skip when the matching task.retried/task.skipped
      // event arrives. The event carries ``original_task_execution_id`` (for
      // retry) or ``task_execution_id`` (for skip, which is the skipped task).
      const current = pendingActionRef.current;
      if (current) {
        if (
          current.action === 'retry_task' &&
          event.type === 'task.retried' &&
          (current.task_execution_id === undefined ||
            current.task_execution_id ===
              (event.payload.original_task_execution_id as string | undefined))
        ) {
          clearPending();
        } else if (
          current.action === 'skip_task' &&
          event.type === 'task.skipped' &&
          (current.task_execution_id === undefined ||
            current.task_execution_id ===
              (event.payload.task_execution_id as string | undefined))
        ) {
          clearPending();
        }
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
  }, [eventQueue, clearQueue, runId, fetchRunDetail, clearPending]);

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

  // Tracks control-action pending state and starts the fallback timer for
  // recovery if the server's ack/error/event never arrives.
  const wrappedSend = useCallback(
    (data: unknown) => {
      if (!data || typeof data !== 'object') {
        send(data);
        return;
      }
      const msg = data as Record<string, unknown>;
      const action = msg.action;
      if (
        action !== 'cancel' &&
        action !== 'abort' &&
        action !== 'pause' &&
        action !== 'retry_task' &&
        action !== 'skip_task'
      ) {
        send(data);
        return;
      }
      const payload = (msg.payload ?? {}) as Record<string, unknown>;
      const taskExecutionId =
        typeof payload.task_execution_id === 'string'
          ? payload.task_execution_id
          : undefined;

      // Drop duplicate sends while the same action is still pending. Without
      // this, a double-click on Retry Task would trigger two executor.retry_task
      // calls server-side and create two task executions.
      const current = pendingActionRef.current;
      if (
        current &&
        current.action === action &&
        current.task_execution_id === taskExecutionId
      ) {
        return;
      }

      send(data);

      const pending: PendingAction = {
        action,
        task_execution_id: taskExecutionId,
        started_at: Date.now(),
      };
      setPendingAction(pending);
      setActionError(null);

      if (pendingFallbackTimer.current !== null) {
        clearTimeout(pendingFallbackTimer.current);
      }
      pendingFallbackTimer.current = setTimeout(() => {
        pendingFallbackTimer.current = null;
        if (pendingActionRef.current?.started_at === pending.started_at) {
          fetchRunDetail();
          setPendingAction(null);
        }
      }, 3000);
    },
    [send, fetchRunDetail],
  );

  // Drain the control-message queue: match acks/errors against the current
  // pending action. Keeps the first error to avoid a burst of errors clobbering
  // each other in state.
  useEffect(() => {
    if (controlQueue.length === 0) return;
    const count = controlQueue.length;
    let firstError: ActionErrorState | null = null;
    for (const msg of controlQueue) {
      const current = pendingActionRef.current;
      if (msg.type === 'action_ack') {
        if (
          current &&
          current.action === msg.payload.action &&
          (current.task_execution_id === undefined ||
            current.task_execution_id === msg.payload.task_execution_id)
        ) {
          clearPending();
        }
        continue;
      }
      const errAction = msg.payload.action;
      const errTaskId = msg.payload.task_execution_id;
      // Only match when the error explicitly names the current action. Legacy
      // errors without an `action` field are shown but don't clear pending,
      // so an unsolicited "Invalid JSON" never cancels an in-flight request.
      const matches =
        current !== null &&
        errAction === current.action &&
        (errTaskId === undefined ||
          current.task_execution_id === undefined ||
          errTaskId === current.task_execution_id);
      if (matches) {
        clearPending();
      }
      if (firstError === null) {
        firstError = {
          action: errAction ?? current?.action,
          task_execution_id: errTaskId ?? current?.task_execution_id,
          message: msg.payload.message,
        };
      }
    }
    if (firstError !== null) {
      setActionError(firstError);
    }
    clearControlQueue(count);
  }, [controlQueue, clearControlQueue, clearPending]);

  const dismissActionError = useCallback(() => {
    setActionError(null);
  }, []);

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
    pendingAction,
    actionError,
    dismissActionError,
  };
}
