# [UI-009] API Client + TypeScript Types

## Domain
ui

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: UI-001
- Blocks: UI-003, UI-007, UI-010, UI-011, UI-012

## Spec References
- specs.md Section 10.2 — "REST API"
- specs.md Section 10.3 — "WebSocket Protocol" (event/action types)
- agents/05-ui.md — "API Client (`client.ts`)" and "TypeScript Types (`types.ts`)"

## Summary
Create the REST API client (fetch wrapper) and TypeScript type definitions matching the backend API responses and WebSocket event/action schemas. This is the typed contract between the frontend and backend — every component and hook that communicates with the server depends on these types and this client.

## Acceptance Criteria
- [ ] `ui/src/api/types.ts` exists with all TypeScript interfaces and type aliases
- [ ] `ui/src/api/client.ts` exists with a fetch wrapper and all API endpoint methods
- [ ] Types defined: `DiscoveredFlow`, `FlowError`, `FlowParam`, `FlowRun`, `FlowRunDetail`, `TaskExecution`, `EdgeTransition`, `LogEntry`, `FlowEvent`, `FlowSchedule`, `StartRunRequest`
- [ ] Status types defined: `FlowRunStatus`, `TaskStatus`
- [ ] Client methods cover all REST endpoints from specs.md Section 10.2
- [ ] Client handles JSON parsing and error responses (throws on non-2xx)
- [ ] Client uses relative URLs (e.g., `/api/flows`) so Vite proxy works in dev and same-origin works in production
- [ ] No external HTTP library — uses native `fetch`

## Technical Design

### Files to Create/Modify
- `ui/src/api/types.ts` — all TypeScript type definitions
- `ui/src/api/client.ts` — fetch wrapper + API methods

### Key Implementation Details

#### `types.ts`

```typescript
// --- Status enums as union types ---

export type FlowRunStatus =
    | 'created'
    | 'running'
    | 'paused'
    | 'completed'
    | 'failed'
    | 'cancelled'
    | 'budget_exceeded';

export type TaskStatus =
    | 'pending'
    | 'waiting'
    | 'running'
    | 'completed'
    | 'failed'
    | 'skipped';

export type EdgeType = 'unconditional' | 'conditional' | 'fork' | 'join';

export type NodeType = 'entry' | 'task' | 'exit';

export type ParamType = 'string' | 'number' | 'bool';

// --- API response types ---

export interface FlowError {
    line: number;
    column: number;
    message: string;
    rule?: string;
}

export interface FlowParam {
    name: string;
    type: ParamType;
    default_value?: string | number | boolean;
}

export interface FlowNodeDef {
    name: string;
    type: NodeType;
    prompt: string;
    cwd?: string;
}

export interface FlowEdgeDef {
    source?: string;
    target?: string;
    edge_type: EdgeType;
    condition?: string;
    fork_targets?: string[];
    join_sources?: string[];
}

export interface DiscoveredFlow {
    id: string;
    name: string;
    file_path: string;
    source_dsl: string;
    is_valid: boolean;
    errors: FlowError[];
    params: FlowParam[];
    nodes: FlowNodeDef[];
    edges: FlowEdgeDef[];
    last_modified: string;           // ISO 8601 timestamp
}

export interface FlowRun {
    id: string;
    flow_definition_id: string;
    flow_name: string;
    status: FlowRunStatus;
    elapsed_seconds: number;
    budget_seconds: number;
    params_json?: string;
    started_at?: string;
    completed_at?: string;
    created_at: string;
    error_message?: string;
}

export interface FlowRunDetail extends FlowRun {
    tasks: TaskExecution[];
    edges: EdgeTransition[];
    flow: DiscoveredFlow;           // the flow definition for graph rendering
}

export interface TaskExecution {
    id: string;
    flow_run_id: string;
    node_name: string;
    node_type: NodeType;
    status: TaskStatus;
    generation: number;
    context_mode: string;
    cwd: string;
    started_at?: string;
    completed_at?: string;
    elapsed_seconds?: number;
    exit_code?: number;
    error_message?: string;
    wait_until?: string;
}

export interface EdgeTransition {
    id: string;
    flow_run_id: string;
    from_node: string;
    to_node: string;
    edge_type: EdgeType;
    condition?: string;
    judge_reasoning?: string;
    judge_confidence?: number;
    created_at: string;
}

export interface LogEntry {
    id: number;
    task_execution_id: string;
    log_type: 'stdout' | 'stderr' | 'tool_use' | 'assistant_message' | 'system';
    content: string;
    timestamp: string;
}

export interface FlowSchedule {
    id: string;
    flow_definition_id: string;
    flow_name: string;
    cron_expression: string;
    on_overlap: 'skip' | 'queue' | 'parallel';
    enabled: boolean;
    last_triggered_at?: string;
    next_trigger_at?: string;
    created_at: string;
}

export interface StartRunRequest {
    params?: Record<string, string | number | boolean>;
}

// --- WebSocket event types ---

export interface FlowEvent {
    type: string;
    flow_run_id: string;
    timestamp: string;
    payload: Record<string, unknown>;
}

// --- WebSocket client action types ---

export interface ClientAction {
    action: string;
    flow_run_id: string;
    payload: Record<string, unknown>;
}
```

#### `client.ts`

```typescript
import type {
    DiscoveredFlow,
    FlowRun,
    FlowRunDetail,
    FlowSchedule,
    LogEntry,
    StartRunRequest,
} from './types';

class ApiError extends Error {
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
            post<{ id: string }>(`/api/flows/${flowId}/runs`, req),
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
    },
    schedules: {
        list: () => get<FlowSchedule[]>('/api/schedules'),
        pause: (id: string) => post<void>(`/api/schedules/${id}/pause`),
        resume: (id: string) => post<void>(`/api/schedules/${id}/resume`),
        trigger: (id: string) => post<void>(`/api/schedules/${id}/trigger`),
    },
};
```

**Key design decisions:**
1. **Native `fetch` only** — no axios, no ky. Keeps the dependency footprint minimal.
2. **Relative URLs** — `/api/flows` works with both Vite proxy (dev) and same-origin (production, served by FastAPI).
3. **`ApiError` class** — typed error with `status`, `statusText`, and parsed `body` for error details. Components can catch and inspect.
4. **Generic `request<T>` wrapper** — handles JSON headers, response parsing, and error checking. Returns typed responses.
5. **`api` object** — namespaced methods matching the REST API structure. Easy to discover via autocomplete.

### Edge Cases
- Backend returns non-JSON error body (e.g., HTML 502 from reverse proxy) — the `try/catch` in `request` handles this gracefully
- Backend returns 204 No Content (e.g., after pause/resume) — handled explicitly, returns `undefined`
- Network failure (offline) — `fetch` throws a `TypeError`, not an `ApiError`; components should handle both
- URL encoding: flow IDs and task IDs may contain special characters — for MVP assume UUIDs (no encoding needed), but `encodeURIComponent` can be added if needed
- Query parameter handling for `runs.list(status)` — simple string concatenation is sufficient for a single optional param

## Testing Strategy
1. Type definitions compile without TypeScript errors: `npx tsc --noEmit`
2. API client methods can be imported and called (mock `fetch` with `vi.fn()` or similar)
3. `ApiError` is thrown for non-2xx responses
4. Verify all endpoints from specs.md Section 10.2 have corresponding client methods
