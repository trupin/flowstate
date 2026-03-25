import { useState, useEffect, useCallback } from 'react';
import { api } from '../api/client';
import type { Subtask } from '../api/types';

interface UseSubtasksReturn {
  subtasks: Subtask[];
  loading: boolean;
}

/**
 * Hook that fetches subtasks for a task execution and re-fetches when
 * the subtaskVersion counter changes (incremented by useFlowRun on
 * subtask.updated WebSocket events).
 */
export function useSubtasks(
  runId: string | undefined,
  taskExecutionId: string | undefined,
  subtaskVersion: number,
): UseSubtasksReturn {
  const [subtasks, setSubtasks] = useState<Subtask[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchSubtasks = useCallback(async () => {
    if (!runId || !taskExecutionId) return;
    try {
      const data = await api.runs.subtasks(runId, taskExecutionId);
      setSubtasks(data);
    } catch {
      // Silently ignore fetch errors
    }
  }, [runId, taskExecutionId]);

  // Fetch on mount and when runId/taskExecutionId change
  useEffect(() => {
    if (!runId || !taskExecutionId) {
      setSubtasks([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    fetchSubtasks().finally(() => setLoading(false));
  }, [runId, taskExecutionId, fetchSubtasks]);

  // Re-fetch when subtaskVersion changes (WebSocket event received)
  useEffect(() => {
    if (subtaskVersion === 0) return;
    if (!runId || !taskExecutionId) return;
    void fetchSubtasks();
  }, [subtaskVersion, runId, taskExecutionId, fetchSubtasks]);

  return { subtasks, loading };
}
