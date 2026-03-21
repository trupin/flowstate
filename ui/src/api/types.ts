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

export interface FlowAstJson {
  name: string;
  budget_seconds: number;
  on_error: string;
  context: string;
  workspace: string | null;
  schedule: string | null;
  on_overlap: string;
  skip_permissions: boolean;
  judge: boolean;
  worktree: boolean;
  params: Array<{ name: string; type: string; default: unknown }>;
  nodes: Record<
    string,
    { name: string; node_type: string; prompt: string; cwd?: string }
  >;
  edges: Array<{
    edge_type: string;
    source?: string;
    target?: string;
    condition?: string;
    fork_targets?: string[];
    join_sources?: string[];
  }>;
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
  last_modified: string; // ISO 8601 timestamp
  ast_json?: FlowAstJson; // included by GET /api/flows/:id
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
  worktree_path?: string;
}

export interface FlowRunDetail extends FlowRun {
  tasks: TaskExecution[];
  edges: EdgeTransition[];
  flow: DiscoveredFlow; // the flow definition for graph rendering
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
  task_dir?: string;
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

export interface OrchestratorInfo {
  key: string;
  session_id: string;
  system_prompt: string;
  data_dir: string;
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
