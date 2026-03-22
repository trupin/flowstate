import type {
  DiscoveredFlow,
  FlowRun,
  FlowRunDetail,
  FlowSchedule,
  LogEntry,
  OrchestratorInfo,
  QueuedTask,
  StartRunRequest,
} from './types';

export class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public body?: unknown,
  ) {
    super(`API error ${status}: ${statusText}`);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      // ignore parse errors on error responses
    }
    throw new ApiError(response.status, response.statusText, body);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

function get<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'GET' });
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body ? JSON.stringify(body) : undefined,
  });
}

export const api = {
  flows: {
    list: () => get<DiscoveredFlow[]>('/api/flows'),
    get: (id: string) => get<DiscoveredFlow>(`/api/flows/${id}`),
  },
  runs: {
    list: (status?: string) =>
      get<FlowRun[]>(`/api/runs${status ? `?status=${status}` : ''}`),
    get: (id: string) => get<FlowRunDetail>(`/api/runs/${id}`),
    start: (flowId: string, req: StartRunRequest) =>
      post<{ flow_run_id: string }>(`/api/flows/${flowId}/runs`, req),
    pause: (id: string) => post<void>(`/api/runs/${id}/pause`),
    resume: (id: string) => post<void>(`/api/runs/${id}/resume`),
    cancel: (id: string) => post<void>(`/api/runs/${id}/cancel`),
    retryTask: (runId: string, taskId: string) =>
      post<void>(`/api/runs/${runId}/tasks/${taskId}/retry`),
    skipTask: (runId: string, taskId: string) =>
      post<void>(`/api/runs/${runId}/tasks/${taskId}/skip`),
    taskLogs: (runId: string, taskId: string, after?: string) =>
      get<LogEntry[]>(
        `/api/runs/${runId}/tasks/${taskId}/logs${after ? `?after=${after}` : ''}`,
      ),
    orchestrators: async (runId: string): Promise<OrchestratorInfo[]> => {
      try {
        return await get<OrchestratorInfo[]>(
          `/api/runs/${runId}/orchestrators`,
        );
      } catch {
        return [];
      }
    },
    orchestratorLogs: async (
      runId: string,
      sessionId: string,
    ): Promise<LogEntry[]> => {
      try {
        const data = await get<{ logs: LogEntry[] }>(
          `/api/runs/${runId}/orchestrators/${sessionId}/logs`,
        );
        return data.logs ?? [];
      } catch {
        return [];
      }
    },
  },
  schedules: {
    list: () => get<FlowSchedule[]>('/api/schedules'),
    pause: (id: string) => post<void>(`/api/schedules/${id}/pause`),
    resume: (id: string) => post<void>(`/api/schedules/${id}/resume`),
    trigger: (id: string) => post<void>(`/api/schedules/${id}/trigger`),
  },
  tasks: {
    submit: (
      flowName: string,
      data: {
        title: string;
        description?: string;
        params?: Record<string, unknown>;
        priority?: number;
      },
    ) => post<QueuedTask>(`/api/flows/${flowName}/tasks`, data),
    list: (flowName?: string, status?: string) => {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      const query = params.toString();
      const url = flowName ? `/api/flows/${flowName}/tasks` : '/api/tasks';
      return get<QueuedTask[]>(query ? `${url}?${query}` : url);
    },
    get: (taskId: string) => get<QueuedTask>(`/api/tasks/${taskId}`),
    cancel: (taskId: string) =>
      post<{ status: string }>(`/api/tasks/${taskId}/cancel`, {}),
    update: (
      taskId: string,
      data: Partial<{
        title: string;
        description: string;
        params: Record<string, unknown>;
        priority: number;
      }>,
    ) =>
      request<QueuedTask>(`/api/tasks/${taskId}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    remove: (taskId: string) =>
      request<{ status: string }>(`/api/tasks/${taskId}`, {
        method: 'DELETE',
      }),
    reorder: (flowName: string, taskIds: string[]) =>
      post<{ status: string }>(`/api/flows/${flowName}/tasks/reorder`, {
        task_ids: taskIds,
      }),
  },
  open: (path: string, command?: string) =>
    post<{ status: string; path: string; command: string }>('/api/open', {
      path,
      command: command ?? localStorage.getItem('flowstate-ide') ?? 'code',
    }),
};
