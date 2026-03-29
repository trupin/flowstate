-- Flow definitions (parsed DSL stored alongside source)
CREATE TABLE IF NOT EXISTS flow_definitions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source_dsl TEXT NOT NULL,
    ast_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Flow runs (execution instances)
CREATE TABLE IF NOT EXISTS flow_runs (
    id TEXT PRIMARY KEY,
    flow_definition_id TEXT NOT NULL REFERENCES flow_definitions(id),
    status TEXT NOT NULL CHECK(status IN (
        'created', 'running', 'paused', 'completed',
        'failed', 'cancelled', 'budget_exceeded'
    )),
    default_workspace TEXT,
    data_dir TEXT NOT NULL,
    params_json TEXT,
    budget_seconds INTEGER NOT NULL,
    elapsed_seconds REAL DEFAULT 0,
    on_error TEXT NOT NULL CHECK(on_error IN ('pause', 'abort', 'skip')),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    error_message TEXT,
    worktree_path TEXT,
    task_id TEXT REFERENCES tasks(id)
);

-- Task executions (individual node runs within a flow run)
CREATE TABLE IF NOT EXISTS task_executions (
    id TEXT PRIMARY KEY,
    flow_run_id TEXT NOT NULL REFERENCES flow_runs(id),
    node_name TEXT NOT NULL,
    node_type TEXT NOT NULL CHECK(node_type IN ('entry', 'task', 'exit', 'wait', 'fence', 'atomic')),
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'waiting', 'running', 'completed', 'failed', 'skipped', 'interrupted'
    )),
    wait_until TIMESTAMP,
    generation INTEGER NOT NULL DEFAULT 1,
    context_mode TEXT NOT NULL CHECK(context_mode IN ('handoff', 'session', 'none')),
    cwd TEXT NOT NULL,
    claude_session_id TEXT,
    task_dir TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    elapsed_seconds REAL,
    exit_code INTEGER,
    summary_path TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Edge transitions (log of every edge traversal)
CREATE TABLE IF NOT EXISTS edge_transitions (
    id TEXT PRIMARY KEY,
    flow_run_id TEXT NOT NULL REFERENCES flow_runs(id),
    from_task_id TEXT NOT NULL REFERENCES task_executions(id),
    to_task_id TEXT REFERENCES task_executions(id),
    edge_type TEXT NOT NULL CHECK(edge_type IN (
        'unconditional', 'conditional', 'fork', 'join'
    )),
    condition_text TEXT,
    judge_session_id TEXT,
    judge_decision TEXT,
    judge_reasoning TEXT,
    judge_confidence REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Fork groups (track parallel execution groups)
CREATE TABLE IF NOT EXISTS fork_groups (
    id TEXT PRIMARY KEY,
    flow_run_id TEXT NOT NULL REFERENCES flow_runs(id),
    source_task_id TEXT NOT NULL REFERENCES task_executions(id),
    join_node_name TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK(status IN ('active', 'joined', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Fork group members
CREATE TABLE IF NOT EXISTS fork_group_members (
    fork_group_id TEXT NOT NULL REFERENCES fork_groups(id),
    task_execution_id TEXT NOT NULL REFERENCES task_executions(id),
    PRIMARY KEY (fork_group_id, task_execution_id)
);

-- Streaming logs from Claude subprocesses
CREATE TABLE IF NOT EXISTS task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_execution_id TEXT NOT NULL REFERENCES task_executions(id),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    log_type TEXT NOT NULL CHECK(log_type IN (
        'stdout', 'stderr', 'tool_use', 'assistant_message', 'system', 'user_input'
    )),
    content TEXT NOT NULL
);

-- Task messages (user messages queued for a task execution)
CREATE TABLE IF NOT EXISTS task_messages (
    id TEXT PRIMARY KEY,
    task_execution_id TEXT NOT NULL REFERENCES task_executions(id),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    processed INTEGER NOT NULL DEFAULT 0
);

-- Flow schedules (recurring flow runs)
CREATE TABLE IF NOT EXISTS flow_schedules (
    id TEXT PRIMARY KEY,
    flow_definition_id TEXT NOT NULL REFERENCES flow_definitions(id),
    cron_expression TEXT NOT NULL,
    on_overlap TEXT NOT NULL DEFAULT 'skip' CHECK(on_overlap IN ('skip', 'queue', 'parallel')),
    enabled INTEGER NOT NULL DEFAULT 1,
    last_triggered_at TIMESTAMP,
    next_trigger_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tasks (work items submitted to a flow's queue)
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    flow_name TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'scheduled', 'queued', 'running', 'waiting', 'completed', 'failed', 'cancelled', 'paused'
    )),
    current_node TEXT,
    params_json TEXT,
    output_json TEXT,
    parent_task_id TEXT REFERENCES tasks(id),
    created_by TEXT,
    flow_run_id TEXT REFERENCES flow_runs(id),
    priority INTEGER DEFAULT 0,
    depth INTEGER DEFAULT 0,
    scheduled_at TIMESTAMP,
    cron_expression TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT
);

-- Task node history (which nodes a task passed through)
CREATE TABLE IF NOT EXISTS task_node_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    node_name TEXT NOT NULL,
    flow_run_id TEXT REFERENCES flow_runs(id),
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- Flow enabled state (runtime toggle, separate from DSL definition)
CREATE TABLE IF NOT EXISTS flow_enabled (
    flow_name TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1
);

-- Task artifacts (structured data stored by agents and the engine per task)
CREATE TABLE IF NOT EXISTS task_artifacts (
    id TEXT PRIMARY KEY,
    task_execution_id TEXT NOT NULL REFERENCES task_executions(id),
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/json',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_execution_id, name)
);

-- Agent subtasks (subtasks created by agents during node execution)
CREATE TABLE IF NOT EXISTS agent_subtasks (
    id TEXT PRIMARY KEY,
    task_execution_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo', 'in_progress', 'done')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_execution_id) REFERENCES task_executions(id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_flow_runs_status ON flow_runs(status);
CREATE INDEX IF NOT EXISTS idx_task_executions_flow_run ON task_executions(flow_run_id);
CREATE INDEX IF NOT EXISTS idx_task_executions_status ON task_executions(flow_run_id, status);
CREATE INDEX IF NOT EXISTS idx_task_executions_waiting ON task_executions(status, wait_until)
    WHERE status = 'waiting';
CREATE INDEX IF NOT EXISTS idx_edge_transitions_flow_run ON edge_transitions(flow_run_id);
CREATE INDEX IF NOT EXISTS idx_task_logs_execution ON task_logs(task_execution_id);
CREATE INDEX IF NOT EXISTS idx_task_logs_timestamp ON task_logs(task_execution_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_task_messages_task ON task_messages(task_execution_id, processed);
CREATE INDEX IF NOT EXISTS idx_fork_groups_flow_run ON fork_groups(flow_run_id);
CREATE INDEX IF NOT EXISTS idx_flow_schedules_next ON flow_schedules(next_trigger_at)
    WHERE enabled = 1;
CREATE INDEX IF NOT EXISTS idx_tasks_flow_name ON tasks(flow_name);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(flow_name, status);
CREATE INDEX IF NOT EXISTS idx_tasks_queue ON tasks(flow_name, status, priority DESC, created_at ASC)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_scheduled ON tasks(status, scheduled_at)
    WHERE status = 'scheduled';
CREATE INDEX IF NOT EXISTS idx_task_node_history_task ON task_node_history(task_id);
CREATE INDEX IF NOT EXISTS idx_task_artifacts_task ON task_artifacts(task_execution_id);
CREATE INDEX IF NOT EXISTS idx_agent_subtasks_task ON agent_subtasks(task_execution_id);
