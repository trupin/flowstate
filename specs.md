# Flowstate Specification

Version 0.1.0 — Draft

---

## 1. Product Overview

### 1.1 What is Flowstate

Flowstate is a state-machine-based orchestration system for AI agents. It lets you define a directed graph where:

- **Nodes** are tasks executed by Claude Code subprocess sessions
- **Edges** are transitions between tasks, evaluated by judge agents
- The graph has a single **entry** node and one or more **exit** nodes
- A custom **DSL** defines the flow, with static analysis that validates correctness before execution

Think of it as a network of AI agents working through a structured process, where the state machine ensures that every handoff between agents is deliberate and validated.

### 1.2 Target User

An individual developer using Flowstate as an internal tool to orchestrate complex multi-step AI-assisted workflows.

### 1.3 Design Principles

- **Transparency**: Every routing decision is logged with reasoning. The full execution history is auditable.
- **Developer control**: Pause, cancel, retry at any point via the web UI.
- **Safety by default**: Budget guards prevent runaway costs. Type checking catches graph errors before execution.
- **Readable definitions**: The DSL makes workflow topology visible at a glance.

### 1.4 Non-Goals (MVP)

- Multi-user / team features
- Cloud deployment or hosted service
- Flow versioning or migration tooling
- Dynamic flow modification at runtime
- Nested / sub-flows (a node that is itself a flow)
- `otherwise` fallback edges (judge returns "no match" → flow pauses)
- Per-task model or tool overrides

---

## 2. Core Concepts

### 2.1 Flow

A named directed graph defining a workflow. A flow has:

- A **name** (identifier)
- A **budget** (wall-clock time limit)
- An optional **workspace** (default working directory for tasks)
- Optional **parameters** (template variables injected into prompts)
- An **on_error** policy (default behavior when a task fails)
- A **context** mode (default context passing strategy)
- One or more **nodes** and **edges**

### 2.2 Node

A vertex in the flow graph. Three types:

| Type | Cardinality | Purpose |
|------|-------------|---------|
| `entry` | Exactly 1 | Starting point. Receives initial parameters. |
| `task` | 0 or more | Intermediate work. Bulk of the flow. |
| `exit` | At least 1 | Terminal point. Flow completes when an exit node finishes. |

Each node has a **prompt** — the instruction given to the Claude Code subprocess — and an optional **cwd** that sets the working directory for that task.

### 2.3 Edge

A directed connection between nodes. Four types:

| Type | Syntax | Semantics |
|------|--------|-----------|
| Unconditional | `A -> B` | B starts when A completes. Only valid when A has exactly 1 outgoing edge. |
| Conditional | `A -> B when "condition"` | A judge evaluates the condition. All outgoing edges from A must be conditional. |
| Fork | `A -> [B, C]` | B and C start in parallel when A completes. |
| Join | `[B, C] -> D` | D starts when both B and C complete. The set must match a prior fork. |

Edges can carry an optional **configuration block** that controls context passing and scheduling.

### 2.4 Task Execution

A running instance of a node. Each task execution:

- Is backed by a Claude Code subprocess with its own session (or a resumed session in `session` mode)
- Runs in the task's **cwd** (from node declaration, or inherited from flow-level `workspace`)
- Has a **generation** counter (incremented on cycle re-entry)
- Has a dedicated **task directory** at `~/.flowstate/runs/<run-id>/tasks/<name>-<generation>/`
- Must write a `SUMMARY.md` to its task directory upon completion
- Streams output to the web UI in real time

### 2.5 Judge

A special-purpose Claude Code subprocess that evaluates conditional edges. After a task completes at a branching node, a judge:

1. Reads the completed task's `SUMMARY.md` from `~/.flowstate/runs/<run-id>/tasks/<name>-<gen>/`
2. Optionally inspects the task's cwd for additional context
3. Evaluates the `when` conditions on all outgoing edges
4. Selects exactly one edge to transition through
5. Records its reasoning for auditability

Judges have **read-only** access to the task's cwd — they observe but don't modify.

### 2.6 Working Directories

Each task runs in its own **cwd** (current working directory). This is where the Claude Code subprocess executes — editing source code, running tests, etc.

**cwd resolution** (in priority order):
1. The task's `cwd` attribute (if declared in the node block)
2. The flow's `workspace` attribute (if declared)
3. Type check error — at least one must be specified

Tasks in the same flow can share a cwd (common for single-repo workflows) or each operate on different directories (multi-repo workflows).

**Parallel safety**: When forked tasks share the same cwd, they should operate on different files. The system does not enforce this — it's a convention.

### 2.6.1 Flowstate Data Directory

All flowstate metadata lives in **`~/.flowstate/`**, completely separated from project directories. Flowstate never writes to a project's working directory (beyond what the Claude Code agent itself does).

```
~/.flowstate/
├── flowstate.db                ← SQLite database (flows, runs, tasks, logs)
├── config.toml                 ← global configuration (optional)
└── runs/
    └── <run-id>/
        └── tasks/
            ├── analyze-1/
            │   ├── SUMMARY.md  ← required: what the task did and its outcome
            │   └── (scratch files)
            ├── implement-1/
            │   └── SUMMARY.md
            ├── implement-2/    ← generation 2 (cycle re-entry)
            │   └── SUMMARY.md
            └── review-1/
                └── SUMMARY.md
```

### 2.9 Context Mode

Determines how context flows from one task to the next along an edge. Two modes:

| Mode | Session | Context source | Fork-join compatible |
|------|---------|---------------|---------------------|
| `handoff` | Fresh session | Previous task's `SUMMARY.md` injected into prompt | Yes |
| `session` | Resumed session (`--resume`) | Full conversation history from previous task | No (linear/conditional only) |

A third option, `none`, starts a fresh session with no upstream context (only the task's own prompt).

**`handoff`** (recommended default): Each task starts a fresh Claude Code session. The previous task's `SUMMARY.md` is read from `~/.flowstate/runs/<run-id>/tasks/` and injected into the new task's prompt. Clean boundaries, predictable context size, works everywhere — including across different working directories.

**`session`**: The next task resumes the previous task's Claude Code session via `--resume <session_id>`. The agent retains full conversation history. Best for linear flows where deep context continuity is critical. **Not allowed on fork edges** — sessions cannot be cloned into parallel instances. Note: if the next task has a different cwd, the resumed session runs in the new cwd.

**`none`**: Fresh session with only the task's own prompt. No upstream context. Useful for tasks that are fully self-contained.

### 2.7 Budget Guard

A wall-clock time limit for a flow run. Claude Code subprocesses don't expose API costs, so time is used as a proxy.

- Tracks cumulative execution time across all tasks
- Emits warnings at 75%, 90%, and 95% of budget
- When budget is exceeded: completes the current task, then pauses the flow
- Does **not** kill tasks mid-execution

### 2.8 Generation

An integer counter per node, starting at 1. Incremented each time the node is re-entered via a cycle. Used to:

- Distinguish repeated executions of the same node
- Match fork-join groups across cycle iterations
- Provide context in the UI ("review, attempt 3")

### 2.10 Scheduling

Flowstate supports three scheduling patterns:

**Edge delays**: An edge can specify a wait before the target task starts. Two forms:
- **Duration** (`delay = 30m`): Wait a fixed amount of time after the source task completes.
- **Cron** (`schedule = "0 2 * * *"`): Wait until the next time matching the cron expression.

Wait time does **not** count toward the flow's budget — only active task execution time does.

**Recurring flow runs**: A flow can declare a `schedule` (cron expression). The flowstate daemon creates a new flow run at each trigger. If a previous run is still active, the `on_overlap` policy applies:

| Policy | Behavior |
|--------|----------|
| `skip` | Skip this trigger. Default. |
| `queue` | Queue the new run to start after the current one finishes. |
| `parallel` | Start a new independent run alongside the existing one. |

**Recurring tasks via cycles + delays**: No dedicated primitive — a cycle edge with a delay naturally creates a polling pattern:

```
monitor -> done when "healthy"
monitor -> monitor when "not yet healthy" {
    delay = 5m
}
```

This executes `monitor` every 5 minutes until the judge decides it's healthy.

---

## 3. DSL Specification

### 3.1 Lexical Structure

```
Comments:       // single-line comments
Strings:        "double quoted" or """triple-quoted multiline"""
Identifiers:    [a-zA-Z_][a-zA-Z0-9_]*
Duration:       <integer>(s|m|h)  — e.g., 2h, 30m, 90s
Path:           "./relative/path" (always quoted)
Keywords:       flow, entry, task, exit, when, param, budget,
                workspace, on_error, context, prompt, cwd,
                schedule, on_overlap, delay
Operators:      ->  =  [  ]  {  }  ,
Template vars:  {{identifier}}
```

### 3.2 Flow Declaration

```
flow <name> {
    budget = <duration>
    on_error = pause | abort | skip
    context = handoff | session | none
    workspace = <path>                    // optional — default cwd for tasks
    schedule = <cron_expression>          // optional — recurring flow
    on_overlap = skip | queue | parallel  // optional — default: skip

    <param_declarations>
    <node_declarations>
    <edge_declarations>
}
```

`budget`, `on_error`, and `context` are required. `workspace` is optional — if present, it serves as the default cwd for tasks that don't declare their own.

`schedule` is optional — if present, the flowstate daemon triggers new runs on the cron schedule. `on_overlap` controls what happens when a trigger fires while a previous run is still active (default: `skip`).

`context` sets the default context mode for all edges (can be overridden per-edge). Recommended default is `handoff`.

`on_error` defines the default behavior when a task fails:

| Policy | Behavior |
|--------|----------|
| `pause` | Pause the flow. User decides via web UI (retry, skip, abort). |
| `abort` | Cancel the entire flow immediately. |
| `skip` | Mark the failed task as skipped and continue to the next edge. |

### 3.3 Parameters

```
param <name>: <type>
param <name>: <type> = <default_value>
```

Supported types: `string`, `number`, `bool`.

Parameters are referenced in prompts via `{{name}}`. They are provided when starting a flow run.

### 3.4 Node Declarations

```
entry <name> {
    prompt = <string>
    cwd = <path>           // optional — overrides flow-level workspace
}

task <name> {
    prompt = <string>
    cwd = <path>           // optional — overrides flow-level workspace
}

exit <name> {
    prompt = <string>
    cwd = <path>           // optional — overrides flow-level workspace
}
```

The prompt can use template variables (`{{param_name}}`) and triple-quoted strings for multiline content.

`cwd` sets the working directory for the Claude Code subprocess. If omitted, the task inherits the flow-level `workspace`. If neither is set, the type checker reports an error. Paths are resolved relative to the `.flow` file's directory.

### 3.5 Edge Declarations

**Unconditional** — simple sequence:
```
analyze -> implement
```

**Conditional** — judge-evaluated branch:
```
review -> done when "all changes are approved and tests pass"
review -> implement when "changes need more work"
```

**Fork** — parallel execution:
```
implement -> [test_unit, test_integration]
```

**Join** — synchronization barrier:
```
[test_unit, test_integration] -> review
```

**Edge with configuration block** (overrides flow-level default):
```
review -> implement when "changes need more work" {
    context = handoff
}
```

**Edge with delay** (wait before target starts):
```
deploy -> check_health {
    delay = 30m
}
```

**Edge with cron schedule** (wait until next cron match):
```
prepare -> deploy {
    schedule = "0 2 * * *"
}
```

`delay` and `schedule` are mutually exclusive on an edge.

### 3.6 Context Modes

Each edge can override the flow-level `context` setting. If omitted, the flow's default applies.

| Mode | Session | What the target task receives | Restrictions |
|------|---------|------------------------------|-------------|
| `handoff` | Fresh | Previous task's `SUMMARY.md` content injected into the prompt | None — works everywhere |
| `session` | Resumed | Full conversation history (continues previous task's Claude Code session) | Not allowed on fork or join edges |
| `none` | Fresh | Only the target task's own prompt | None |

**`handoff` details**: The execution engine reads `SUMMARY.md` from the source task's directory (`~/.flowstate/runs/<run-id>/tasks/<name>-<gen>/SUMMARY.md`) and injects it into the target task's prompt as a "Context from previous task" section.

**`session` details**: The target task resumes the source task's Claude Code session using `--resume <session_id>`. The new task's prompt is sent as a follow-up message in the existing conversation. Context grows across the session chain.

**At join edges**: Context from all completed fork members is aggregated. Each member's `SUMMARY.md` is injected into the join target's prompt. Session mode is not available at joins (multiple sessions cannot merge).

**Summary requirement**: Regardless of context mode, every task **must** write a `SUMMARY.md` to its task directory under `~/.flowstate/`. This is enforced by including an instruction in every task prompt. The summary serves as:
- Input for the judge agent at conditional edges
- Context for downstream tasks in `handoff` mode
- Audit trail for debugging and the web UI

The task prompt tells the agent the absolute path to its task directory (e.g., `~/.flowstate/runs/abc123/tasks/implement-1/`).

### 3.7 Complete Example

```
flow code_review {
    budget = 2h
    on_error = pause
    context = handoff
    workspace = "./project"

    param focus: string = "all"

    entry analyze {
        prompt = """
        Analyze the codebase and identify areas for improvement.
        Focus on: {{focus}}.
        Create a file PLAN.md with your findings and proposed changes.
        """
    }

    task implement {
        prompt = """
        Read PLAN.md and implement the proposed changes.
        Work through each item methodically.
        """
    }

    task test_unit {
        prompt = "Run the unit test suite. Fix any failures caused by the recent changes."
    }

    task test_integration {
        prompt = "Run integration tests. Verify system behavior end-to-end."
    }

    task review {
        prompt = """
        Review all changes made since the last review.
        Check for correctness, code quality, and test coverage.
        """
    }

    exit summarize {
        prompt = "Write a summary of all changes to CHANGELOG.md."
    }

    // Flow
    analyze -> implement {
        context = session    // implement continues analyze's conversation
    }
    implement -> [test_unit, test_integration]
    [test_unit, test_integration] -> review
    review -> summarize when "all changes are approved and tests pass"
    review -> implement when "changes need more work"
}
```

---

## 4. Type System and Static Analysis

The type checker validates a parsed flow AST before execution. All rules produce errors (not warnings) and prevent the flow from starting.

### 4.1 Structural Rules

| # | Rule | Rationale |
|---|------|-----------|
| S1 | Exactly one `entry` node | Unambiguous starting point |
| S2 | At least one `exit` node | Flow must be able to terminate |
| S3 | All nodes reachable from `entry` | No dead/orphan nodes |
| S4 | At least one `exit` reachable from every node | Every node can eventually lead to termination |
| S5 | No duplicate node names | Names are identifiers used in edges |
| S6 | `entry` node has no incoming edges | Nothing transitions into the start |
| S7 | `exit` nodes have no outgoing edges | Nothing transitions out of a terminal |
| S8 | Every node must have a resolvable cwd (own `cwd` or flow-level `workspace`) | Tasks need a working directory |

### 4.2 Edge Rules

| # | Rule | Rationale |
|---|------|-----------|
| E1 | Node with 1 outgoing edge: must be unconditional | No judge needed for a single path |
| E2 | Node with 2+ outgoing edges: all must be conditional (`when`) OR all must be a single fork | No ambiguity in edge semantics |
| E3 | Fork and conditional edges cannot be mixed from the same node | A node is either a branch-point or a fork-point |
| E4 | Every edge references existing nodes | No dangling references |
| E5 | Fork target set must have exactly one matching join with the same node set | Every fork must close |
| E6 | Join source set must match exactly one prior fork's target set | Every join must correspond to a fork |
| E7 | `context = session` is not allowed on fork or join edges | Sessions cannot be cloned into parallel instances or merged |
| E8 | `delay` and `schedule` are mutually exclusive on an edge | An edge can wait for a duration or a cron match, not both |
| E9 | `schedule` (cron) on an edge must be a valid cron expression | Prevents runtime errors from bad cron syntax |

### 4.3 Cycle Rules

| # | Rule | Rationale |
|---|------|-----------|
| C1 | Cycle targets must be outside any fork-join group | Cycling into the middle of a fork group creates ambiguous join semantics |
| C2 | Every cycle must pass through at least one conditional edge | Prevents unconditional infinite loops — a judge must decide to re-enter |
| C3 | Flows with cycles must declare a `budget` | Time guard is mandatory for potentially-infinite execution |

**C1 explained**: If nodes B and C are forked (`A -> [B, C]`) and joined (`[B, C] -> D`), no cycle edge may target B or C directly. The cycle must target A (the fork source) or any node before A.

### 4.4 Fork-Join Rules

| # | Rule | Rationale |
|---|------|-----------|
| F1 | Fork groups may be nested but must not partially overlap | Clean scoping of parallel regions |
| F2 | A join node cannot also be a fork source in the same declaration | Separate the join and the next fork into distinct edge declarations |
| F3 | Fork targets must eventually converge to a single join | No "fire and forget" parallel paths |

**Nested fork example** (valid):
```
A -> [B, C]
B -> [D, E]
[D, E] -> B_done
[B_done, C] -> F
```

**Partial overlap** (invalid):
```
A -> [B, C]
B -> [C, D]     // C appears in two fork groups — invalid
```

### 4.5 Validation Algorithm

```
1. Build adjacency list from edges
2. Verify S1-S7 (structural)
3. For each node, classify outgoing edges and verify E1-E7
4. Identify all fork-join pairs, verify F1-F3
5. Detect cycles via DFS, verify C1-C3
6. Verify reachability (S3-S4) via BFS from entry
```

---

## 5. Architecture

### 5.1 Component Overview

```
┌──────────────────────────────────────────────┐
│              Web UI (React)                  │
│    Graph Viz  │  Log Viewer  │  Controls     │
└──────────┬───────────────────────────────────┘
           │ WebSocket + REST
┌──────────▼───────────────────────────────────┐
│           Web Server (FastAPI)               │
│    REST API  │  WebSocket Hub                │
└──────────┬───────────────────────────────────┘
           │
     ┌─────┼──────────────┬───────────────┐
     │     │              │               │
┌────▼───┐ ┌──▼────────┐ ┌──▼──────────┐ ┌──▼──────────┐
│Execution│ │   State   │ │   Budget    │ │   File      │
│ Engine  │ │  Manager  │ │   Guard     │ │  Watcher    │
│(asyncio)│ │ (SQLite)  │ │(time track) │ │(watchfiles) │
└────┬────┘ └───────────┘ └─────────────┘ └─────────────┘
     │
┌────▼──────────────────────────────────┐
│     Claude Code Subprocesses          │
│  ┌──────┐ ┌──────┐ ┌───────┐         │
│  │Task A│ │Task B│ │ Judge │ ...      │
│  └──┬───┘ └──┬───┘ └───┬───┘         │
│     └────────┴─────────┘              │
│              │                        │
│     ┌────────▼────────┐              │
│     │   Workspace     │              │
│     │ (shared files)  │              │
│     └─────────────────┘              │
└───────────────────────────────────────┘
```

### 5.2 Parser

- **Input**: DSL source text (`.flow` file)
- **Output**: AST (Python dataclasses)
- **Technology**: Lark with Earley parser (handles ambiguity gracefully)
- **Error reporting**: Line and column numbers with descriptive messages

### 5.3 Type Checker

- **Input**: AST from parser
- **Output**: Validated AST or list of typed errors
- **Implements**: All rules from Section 4
- **Graph algorithms**: BFS/DFS for reachability, cycle detection; set matching for fork-join pairs

### 5.4 Execution Engine

- **Input**: Validated AST + parameter values
- **Manages**: Claude Code subprocess lifecycle, fork-join coordination, judge invocation, cycle tracking
- **Concurrency**: Python `asyncio` with `asyncio.create_subprocess_exec`
- **Parallelism**: Semaphore-bounded concurrent subprocesses (configurable, default 4)

### 5.5 State Manager

- **Storage**: SQLite with WAL mode
- **Atomicity**: State transitions (task status change + edge creation) are single transactions
- **Recovery**: On restart, detect in-flight runs and mark orphaned tasks as failed

### 5.6 Budget Guard

- **Implementation**: Background `asyncio` task polling cumulative elapsed time
- **Thresholds**: Warnings at 75%, 90%, 95%
- **Enforcement**: Pauses flow after current task completes (no mid-task kills)

### 5.6.1 Scheduler

- **Implementation**: Background `asyncio` task, checks every 30 seconds
- **Edge delays**: Queries `task_executions` for `status = 'waiting'` where `wait_until <= now()`, transitions them to `pending`
- **Recurring flows**: Queries `flow_schedules` for `next_trigger_at <= now()`, applies overlap policy, creates new flow runs
- **Cron parsing**: Uses `croniter` library for cron expression evaluation

### 5.7 Web Server

- **Framework**: FastAPI with uvicorn
- **REST**: Flow CRUD, run management, task inspection
- **WebSocket**: Real-time streaming of execution events
- **Static files**: Serves the built React frontend

### 5.8 Web UI

- **Framework**: React + TypeScript
- **Graph rendering**: React Flow (with dagre or elkjs for automatic layout)
- **Layout**: Split pane — graph on left, log viewer on right
- **Updates**: Real-time via WebSocket

---

## 6. Execution Model

### 6.1 Flow Run Lifecycle

```
                    ┌──► Completed
                    │
Created ──► Running ┼──► Failed
                │   │
                │   └──► Budget Exceeded
                │
                ▼
             Paused ──► Cancelled
                │
                └──────► Running (resumed)
```

| Status | Meaning |
|--------|---------|
| `created` | Flow run record exists, not yet started |
| `running` | At least one task is pending or executing |
| `paused` | Execution suspended (user action, error, or budget) |
| `completed` | An exit node finished successfully |
| `failed` | Unrecoverable error (or user chose abort) |
| `cancelled` | User cancelled via UI |
| `budget_exceeded` | Budget limit reached, execution paused |

### 6.2 Task Execution Lifecycle

```
Pending ──► Waiting ──► Running ──► Completed
               │              │
               │              └──► Failed ──► [User Decision]
               │                                    │
               └─ (delay/schedule elapsed)    ┌─────┼──────┐
                                              ▼     ▼      ▼
                                           Retry  Skip   Abort
```

| Status | Meaning |
|--------|---------|
| `pending` | Dependencies met, ready to run (or waiting for semaphore) |
| `waiting` | Delayed — a `delay` or `schedule` on the incoming edge hasn't elapsed yet |
| `running` | Claude Code subprocess is executing |
| `completed` | Task finished successfully |
| `failed` | Task errored |
| `skipped` | User chose to skip a failed task |

### 6.3 Execution Algorithm

```python
async def execute_flow(flow_ast, params):
    # 1. Create flow run record
    run = create_flow_run(flow_ast, params)

    # 2. Expand template variables in all prompts
    expand_templates(flow_ast, params)

    # 3. Create initial task for entry node
    enqueue_task(run, entry_node, generation=1)

    # 4. Main loop
    while has_pending_or_running_tasks(run):
        # Pick all pending tasks whose dependencies are satisfied
        ready = get_ready_tasks(run)

        for task in ready:
            # Respect concurrency limit
            await semaphore.acquire()
            asyncio.create_task(execute_task(run, task))

        # Wait for at least one task to complete
        completed = await wait_for_any_completion(run)

        for task in completed:
            semaphore.release()

            if task.status == "failed":
                handle_error(run, task)  # applies on_error policy
                continue

            # Evaluate outgoing edges
            outgoing = get_outgoing_edges(flow_ast, task.node_name)

            if is_unconditional(outgoing):
                enqueue_task(run, outgoing[0].target, generation=next_gen(task),
                             edge=outgoing[0])

            elif is_fork(outgoing):
                group = create_fork_group(run, task, outgoing.targets)
                for target in outgoing.targets:
                    enqueue_task(run, target, generation=next_gen(task),
                                fork_group=group, edge=outgoing_for(target))

            elif is_conditional(outgoing):
                decision = await invoke_judge(run, task, outgoing)
                if decision == "__none__":
                    pause_flow(run, reason="Judge could not match any condition")
                else:
                    enqueue_task(run, decision.target, generation=next_gen(task),
                                edge=decision.edge)

            # Check if this completes a fork-join group
            if task_in_fork_group(task):
                group = get_fork_group(task)
                if all_members_completed(group):
                    join_target = get_join_target(flow_ast, group)
                    enqueue_task(run, join_target, generation=next_gen(task))

            # Budget check
            if budget_exceeded(run):
                pause_flow(run, reason="Budget exceeded")
                break

        # Check for exit node completion
        if exit_node_completed(run):
            complete_flow(run)
            break

    return run

# enqueue_task handles scheduling:
def enqueue_task(run, node, generation, edge=None, fork_group=None):
    task = create_task_execution(run, node, generation)
    if edge and edge.config.delay:
        task.status = "waiting"
        task.wait_until = now() + edge.config.delay
    elif edge and edge.config.schedule:
        task.status = "waiting"
        task.wait_until = next_cron_match(edge.config.schedule)
    else:
        task.status = "pending"
```

### 6.8 Edge Delays

When a task is created with status `waiting`:

1. The scheduler (background asyncio task) periodically checks for waiting tasks whose `wait_until` has elapsed
2. When elapsed: transition status from `waiting` to `pending`
3. The main loop picks it up as a ready task
4. Wait time does **not** count toward the flow's budget
5. The web UI shows a countdown/next-trigger time for waiting tasks

### 6.9 Recurring Flow Runs

When a flow declares `schedule`:

1. The daemon stores the schedule in the `flow_schedules` table
2. A background scheduler checks cron expressions every minute
3. On trigger:
   - Check `on_overlap` policy
   - If `skip` and a run is active: do nothing
   - If `queue`: create run with status `created`, start when previous finishes
   - If `parallel`: create and start immediately
4. The daemon emits a `flow.scheduled_trigger` event for the web UI

### 6.4 Fork-Join Execution

1. **Fork**: Source task completes → all target tasks created as `pending` simultaneously → a `fork_group` record links them
2. **Parallel execution**: Ready tasks are picked up concurrently (up to semaphore limit)
3. **Join**: Each fork member completes → check if all members of the fork group are `completed` → if yes, create pending task for the join target
4. **Generation**: All tasks in a fork group share the same generation. The join target gets `generation + 1`.

### 6.5 Conditional Branching

1. Source task completes at a node with conditional outgoing edges
2. Execution engine reads the task's `SUMMARY.md` from `.flowstate/tasks/<name>-<gen>/`
3. Execution engine spawns a **judge** subprocess with the summary as context
4. Judge evaluates conditions and returns structured decision (target node + reasoning + confidence)
5. If `confidence < 0.5`: pause flow for human review (even though a decision was returned)
6. If decision is `__none__`: pause flow
7. Otherwise: create pending task for the chosen target, using the edge's context mode

### 6.6 Cycle Re-entry

When a conditional edge targets an already-executed node:

1. Increment the generation counter for that node
2. Create a new `task_execution` record with the new generation
3. Create a new task directory: `.flowstate/tasks/<name>-<new_gen>/`
4. Context depends on the edge's mode:
   - `handoff` (default): Fresh session. The previous task's summary + judge feedback injected into prompt.
   - `session`: Resume the previous task's session. The new prompt is sent as a follow-up.
   - `none`: Fresh session with only the task's own prompt.

**Note on `session` mode and cycles**: If a cycle edge uses `session` mode, the re-entered task resumes the *source* task's session (the task that triggered the cycle), not its own previous session. This means the agent that did the review continues into the implementation, carrying its full review context.

### 6.7 Concurrency Controls

- **Max concurrent tasks**: Configurable (default 4). Enforced via `asyncio.Semaphore`.
- **Judge calls**: Count toward the concurrency limit (they are also subprocesses).
- **Paused flows**: Release all semaphore slots. No subprocesses run while paused.

---

## 7. Judge Protocol

### 7.1 Judge Prompt Template

```
You are a routing judge for the Flowstate orchestration system.

## Completed Task
- Name: {node_name}
- Prompt: {task_prompt}
- Exit Code: {exit_code}

## Task Summary
The following summary was written by the task agent:

---
{contents of ~/.flowstate/runs/{run_id}/tasks/{node_name}-{generation}/SUMMARY.md}
---

## Task Working Directory
The task ran in: {task_cwd}
You have read-only access. You may inspect files beyond the summary
if needed for your decision.

## Available Transitions
{for each outgoing edge}
- "{when_condition}" → transitions to: {target_node}
{end for}

## Instructions
Based on the task summary and workspace state, determine which transition
condition best matches the current state of the work. You MUST select
exactly one target. If no condition clearly matches, select "__none__".
```

### 7.2 Judge Output Schema

```json
{
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["<target_1>", "<target_2>", ..., "__none__"]
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of why this transition was chosen"
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "How confident the judge is in this decision"
        }
    },
    "required": ["decision", "reasoning", "confidence"]
}
```

### 7.3 Judge Invocation

The judge is a Claude Code subprocess with restricted permissions:

```bash
claude -p "<judge_prompt>" \
    --output-format json \
    --permission-mode plan \
    --model sonnet
```

The judge runs in the completed task's cwd so it can inspect files.

Key constraints:
- `--permission-mode plan`: Read-only access (the judge must not modify files)
- `--model sonnet`: Faster and cheaper than opus for classification decisions
- Output is parsed as JSON matching the schema above

### 7.4 Judge Failure Handling

| Failure | Response |
|---------|----------|
| Subprocess crashes (non-zero exit) | Retry once. If retry fails, pause flow. |
| Output doesn't match schema | Retry once. If retry fails, pause flow. |
| Decision is `__none__` | Pause flow. User decides via web UI. |
| Confidence < 0.5 | Pause flow with the tentative decision shown. User can accept or override. |

---

## 8. State Management

### 8.1 SQLite Schema

```sql
-- Flow definitions (parsed DSL stored alongside source)
CREATE TABLE flow_definitions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source_dsl TEXT NOT NULL,
    ast_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Flow runs (execution instances)
CREATE TABLE flow_runs (
    id TEXT PRIMARY KEY,
    flow_definition_id TEXT NOT NULL REFERENCES flow_definitions(id),
    status TEXT NOT NULL CHECK(status IN (
        'created', 'running', 'paused', 'completed',
        'failed', 'cancelled', 'budget_exceeded'
    )),
    default_workspace TEXT,         -- optional flow-level workspace (may be NULL)
    data_dir TEXT NOT NULL,         -- ~/.flowstate/runs/<id>/
    params_json TEXT,
    budget_seconds INTEGER NOT NULL,
    elapsed_seconds REAL DEFAULT 0,
    on_error TEXT NOT NULL CHECK(on_error IN ('pause', 'abort', 'skip')),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    error_message TEXT
);

-- Task executions (individual node runs within a flow run)
CREATE TABLE task_executions (
    id TEXT PRIMARY KEY,
    flow_run_id TEXT NOT NULL REFERENCES flow_runs(id),
    node_name TEXT NOT NULL,
    node_type TEXT NOT NULL CHECK(node_type IN ('entry', 'task', 'exit')),
    status TEXT NOT NULL CHECK(status IN (
        'pending', 'waiting', 'running', 'completed', 'failed', 'skipped'
    )),
    wait_until TIMESTAMP,           -- NULL if not delayed; set for waiting tasks
    generation INTEGER NOT NULL DEFAULT 1,
    context_mode TEXT NOT NULL CHECK(context_mode IN ('handoff', 'session', 'none')),
    cwd TEXT NOT NULL,              -- resolved working directory for this task
    claude_session_id TEXT,
    task_dir TEXT NOT NULL,         -- ~/.flowstate/runs/<run-id>/tasks/<name>-<gen>/
    prompt_text TEXT NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    elapsed_seconds REAL,
    exit_code INTEGER,
    summary_path TEXT,              -- path to SUMMARY.md (set on completion)
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Edge transitions (log of every edge traversal)
CREATE TABLE edge_transitions (
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
CREATE TABLE fork_groups (
    id TEXT PRIMARY KEY,
    flow_run_id TEXT NOT NULL REFERENCES flow_runs(id),
    source_task_id TEXT NOT NULL REFERENCES task_executions(id),
    join_node_name TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK(status IN ('active', 'joined', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Fork group members
CREATE TABLE fork_group_members (
    fork_group_id TEXT NOT NULL REFERENCES fork_groups(id),
    task_execution_id TEXT NOT NULL REFERENCES task_executions(id),
    PRIMARY KEY (fork_group_id, task_execution_id)
);

-- Streaming logs from Claude subprocesses
CREATE TABLE task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_execution_id TEXT NOT NULL REFERENCES task_executions(id),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    log_type TEXT NOT NULL CHECK(log_type IN (
        'stdout', 'stderr', 'tool_use', 'assistant_message', 'system'
    )),
    content TEXT NOT NULL
);

-- Flow schedules (recurring flow runs)
CREATE TABLE flow_schedules (
    id TEXT PRIMARY KEY,
    flow_definition_id TEXT NOT NULL REFERENCES flow_definitions(id),
    cron_expression TEXT NOT NULL,
    on_overlap TEXT NOT NULL DEFAULT 'skip' CHECK(on_overlap IN ('skip', 'queue', 'parallel')),
    enabled INTEGER NOT NULL DEFAULT 1,    -- 0 = paused
    last_triggered_at TIMESTAMP,
    next_trigger_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_flow_runs_status ON flow_runs(status);
CREATE INDEX idx_task_executions_flow_run ON task_executions(flow_run_id);
CREATE INDEX idx_task_executions_status ON task_executions(flow_run_id, status);
CREATE INDEX idx_task_executions_waiting ON task_executions(status, wait_until)
    WHERE status = 'waiting';
CREATE INDEX idx_edge_transitions_flow_run ON edge_transitions(flow_run_id);
CREATE INDEX idx_task_logs_execution ON task_logs(task_execution_id);
CREATE INDEX idx_task_logs_timestamp ON task_logs(task_execution_id, timestamp);
CREATE INDEX idx_fork_groups_flow_run ON fork_groups(flow_run_id);
CREATE INDEX idx_flow_schedules_next ON flow_schedules(next_trigger_at)
    WHERE enabled = 1;
```

### 8.2 Transaction Boundaries

| Operation | Transaction Scope |
|-----------|-------------------|
| Task status change + edge creation | Single transaction |
| Fork group creation + all member inserts | Single transaction |
| Log insertion | Individual transactions (high frequency, acceptable to lose on crash) |
| Flow status change | Single transaction with elapsed_seconds update |

### 8.3 Recovery Protocol

On process restart:

1. Query `flow_runs` with `status = 'running'`
2. For each: check if Claude subprocess PIDs are still alive
3. Mark orphaned `task_executions` (status `running` but no live process) as `failed`
4. Set flow status to `paused` with `error_message = "Recovered after process restart"`
5. User can resume or abort via the web UI

### 8.4 Database Configuration

- **WAL mode**: Enabled for concurrent read/write (execution engine writes while web server reads)
- **Journal size limit**: 64MB (prevents unbounded WAL growth)
- **Busy timeout**: 5000ms (handles brief write contention during parallel tasks)

---

## 9. Claude Code Integration

### 9.1 Task Subprocess Invocation

Two invocation patterns depending on context mode:

**`handoff` or `none` mode** (fresh session):
```bash
claude -p "<composed_prompt>" \
    --output-format stream-json
```

**`session` mode** (resume previous session):
```bash
claude -p "<followup_prompt>" \
    --output-format stream-json \
    --resume <previous_session_id>
```

The subprocess is started from the task's resolved cwd (from node `cwd` attribute, or flow-level `workspace`).

**Prompt construction for `handoff` mode**:

```
You are executing a task in a Flowstate workflow.

## Context from previous task
{contents of ~/.flowstate/runs/<run-id>/tasks/<prev_name>-<prev_gen>/SUMMARY.md}

## Your task
{Node prompt with {{params}} expanded}

## Working directory
Your working directory is: {resolved_cwd}

## Task directory
Write your working notes and scratch files to {task_dir}/.
When you are done, you MUST write a SUMMARY.md to {task_dir}/SUMMARY.md describing:
- What you did
- What changed
- The outcome / current state
```

**Prompt construction for `session` mode**:

```
## Next task: {node_name}
{Node prompt with {{params}} expanded}

When you are done, write a SUMMARY.md to {task_dir}/SUMMARY.md
describing what you did and the outcome.
```

(Shorter because the full conversation context is already present in the resumed session.)

**Prompt construction for join nodes** (`handoff` mode with multiple predecessors):

```
You are executing a task in a Flowstate workflow.

## Context from parallel tasks

### {fork_member_1_name}
{contents of ~/.flowstate/runs/<run-id>/tasks/<member1>-<gen>/SUMMARY.md}

### {fork_member_2_name}
{contents of ~/.flowstate/runs/<run-id>/tasks/<member2>-<gen>/SUMMARY.md}

## Your task
{Node prompt with {{params}} expanded}

## Working directory and task directory
[same as above]
```

### 9.2 Output Capture

With `--output-format stream-json`, Claude Code emits one JSON object per line on stdout. Each object has a `type` field. Relevant types:

| Type | Content | Use |
|------|---------|-----|
| `assistant` | Text from the model | Display in log viewer, capture for context passing |
| `tool_use` | Tool invocation | Display in log viewer |
| `tool_result` | Tool output | Display in log viewer |
| `error` | Error message | Detect failures |
| `result` | Final result | Task completion output |

The execution engine:

1. Pipes stdout through an async reader
2. Parses each JSON line
3. Writes to `task_logs` table
4. Forwards to WebSocket hub for live UI updates
5. On process exit: checks exit code (0 = success, non-zero = failure)

### 9.3 Session Management

- **`handoff`/`none` mode**: Each task execution gets a fresh Claude Code session. Session ID is a generated UUID stored in `task_executions.claude_session_id`.
- **`session` mode**: The task resumes the previous task's session via `--resume`. The `claude_session_id` in `task_executions` stores the resumed session ID (same as the source task's session).
- **Session chains**: In `session` mode, a chain of tasks (A → B → C) all share one growing conversation. The session ID is A's original session, resumed by B, then by C.
- **Context window risk**: Long session chains can exceed the model's context window. This is the user's responsibility to manage. The engine does not enforce a session chain length limit.

### 9.5 Task Directory Setup

Before launching a task subprocess, the execution engine:

1. Creates the run directory if needed: `~/.flowstate/runs/<run-id>/`
2. Creates the task directory: `~/.flowstate/runs/<run-id>/tasks/<name>-<gen>/`
3. Resolves the task's cwd (node `cwd` → flow `workspace` → error)
4. The task prompt includes the absolute path to the task directory so the agent can write `SUMMARY.md`

### 9.4 Error Detection

| Signal | Meaning | Response |
|--------|---------|----------|
| Exit code 0 | Task completed successfully | Proceed to edge evaluation |
| Exit code non-zero | Task failed | Apply `on_error` policy |
| Process killed by signal | Crash or OOM | Mark as failed, apply `on_error` policy |
| No output for > 5 minutes | Possible hang | Log warning (but don't kill — the task may be doing legitimate work) |

---

## 10. Web Interface

Clean dashboard UI. Dark mode only. Desktop-only layout.

### 10.1 Pages

| Page | Purpose |
|------|---------|
| Flow Library | List discovered `.flow` files from watched directory. Click to see graph preview. Shows parse/type-check errors inline. "Start Run" button opens modal. **No DSL editor** — flows are edited externally and auto-discovered via file watcher. |
| Run Detail | Graph left (~60%) + log viewer right (~40%) + control bar bottom. Live visualization + streaming logs + flow controls. Active runs are accessed from the sidebar (no separate Run Dashboard page). |

### 10.2 REST API

Flows are discovered from the filesystem (watched directory), not created manually via the API.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/flows` | List discovered flows from watched directory (parsed, with error state) |
| `GET` | `/api/flows/:id` | Get flow definition with DSL source and parse status |
| `POST` | `/api/flows/:id/runs` | Start a new run (body: params) |
| `GET` | `/api/runs` | List all runs (filterable by status) |
| `GET` | `/api/runs/:id` | Get run details + task executions + edges |
| `POST` | `/api/runs/:id/pause` | Pause a running flow |
| `POST` | `/api/runs/:id/resume` | Resume a paused flow |
| `POST` | `/api/runs/:id/cancel` | Cancel a flow |
| `POST` | `/api/runs/:id/tasks/:task_id/retry` | Retry a failed task |
| `POST` | `/api/runs/:id/tasks/:task_id/skip` | Skip a failed task |
| `GET` | `/api/runs/:id/tasks/:task_id/logs` | Get task logs (paginated) |
| `GET` | `/api/schedules` | List all flow schedules |
| `POST` | `/api/schedules/:id/pause` | Pause a recurring schedule |
| `POST` | `/api/schedules/:id/resume` | Resume a paused schedule |
| `POST` | `/api/schedules/:id/trigger` | Manually trigger a scheduled flow |

### 10.3 WebSocket Protocol

**Connection**: `ws://localhost:<port>/ws`

**Server → Client events**:

```json
{
    "type": "<event_type>",
    "flow_run_id": "<uuid>",
    "timestamp": "<iso8601>",
    "payload": { }
}
```

| Event Type | Payload | When |
|------------|---------|------|
| `flow.started` | `{status, budget_seconds}` | Flow run begins |
| `flow.status_changed` | `{old_status, new_status, reason}` | Any flow status change |
| `flow.completed` | `{elapsed_seconds, final_status}` | Flow finishes |
| `flow.budget_warning` | `{elapsed_seconds, budget_seconds, percent_used}` | At 75%, 90%, 95% of budget |
| `task.started` | `{task_execution_id, node_name, generation}` | Task begins |
| `task.log` | `{task_execution_id, log_type, content}` | Streaming output line |
| `task.completed` | `{task_execution_id, node_name, exit_code, elapsed_seconds}` | Task finishes |
| `task.failed` | `{task_execution_id, node_name, error_message}` | Task errors |
| `edge.transition` | `{from_node, to_node, edge_type, condition, judge_reasoning}` | Edge traversal |
| `fork.started` | `{fork_group_id, source_node, targets: [...]}` | Fork begins |
| `fork.joined` | `{fork_group_id, join_node}` | All fork members done |
| `judge.started` | `{from_node, conditions: [...]}` | Judge evaluation begins |
| `judge.decided` | `{from_node, to_node, reasoning, confidence}` | Judge made decision |
| `task.waiting` | `{task_execution_id, node_name, wait_until, reason}` | Task is delayed |
| `task.wait_elapsed` | `{task_execution_id, node_name}` | Delay elapsed, task now pending |
| `schedule.triggered` | `{flow_definition_id, flow_run_id, cron_expression}` | Recurring flow triggered |
| `schedule.skipped` | `{flow_definition_id, reason}` | Trigger skipped (overlap policy) |
| `flow.file_changed` | `{file_path, flow_name}` | A `.flow` file was modified on disk |
| `flow.file_error` | `{file_path, flow_name, errors: [...]}` | A `.flow` file has parse/type errors after change |
| `flow.file_valid` | `{file_path, flow_name}` | A previously broken `.flow` file is now valid |

**Client → Server actions**:

```json
{
    "action": "<action_type>",
    "flow_run_id": "<uuid>",
    "payload": { }
}
```

| Action | Payload | Effect |
|--------|---------|--------|
| `subscribe` | `{flow_run_id, last_event_timestamp?}` | Subscribe to a run's events. Replays missed events if timestamp provided. |
| `unsubscribe` | `{flow_run_id}` | Stop receiving events |
| `pause` | `{}` | Pause after current task(s) complete |
| `cancel` | `{}` | Cancel the flow run |
| `retry_task` | `{task_execution_id}` | Retry a failed task |
| `skip_task` | `{task_execution_id}` | Skip and continue |
| `abort` | `{}` | Immediately kill all subprocesses and cancel |

**Reconnection**: On WebSocket reconnect, client sends `subscribe` with `last_event_timestamp`. Server replays all events after that timestamp from the database.

### 10.4 Graph Visualization

- **Layout**: Automatic layout via dagre (top-to-bottom directed graph)
- **Node design (hybrid)**: Compact pills by default (name + status color fill). Click to expand with metadata (type badge, generation count, elapsed time, cwd). Hover tooltip for quick info without expanding.
- **Node colors**:

| Status | Color |
|--------|-------|
| Pending | Gray |
| Waiting | Purple (with countdown timer) |
| Running | Blue (animated pulse) |
| Completed | Green |
| Failed | Red |
| Skipped | Orange |
| Paused | Yellow |

- **Edge rendering**: Animated flow direction. Conditional edges labeled with truncated `when` text. Fork/join edges visually grouped.
- **Active task highlight**: The currently running task node is enlarged with a glow effect.
- **Generation badge**: Nodes re-entered via cycles show a badge with the generation count.

### 10.5 Log Viewer

- Click a node in the graph → log viewer shows that task's streaming output
- Raw streaming output, line by line
- Monospace font, dark background
- Real-time auto-scroll with pin-to-bottom toggle
- No structured parsing of tool_use/tool_result — keep it simple, show raw output

### 10.6 Control Panel

- **Pause / Resume**: Toggle flow execution
- **Cancel**: Stop the flow entirely
- **Retry / Skip**: Available on failed tasks
- **Budget**: Shows elapsed vs. budget with progress bar

### 10.7 Sidebar

The sidebar provides navigation across all three sections of the app:

```
┌───────────────────┐
│  FLOWSTATE        │
│                   │
│  FLOWS            │
│   code_review  ●  │  ← green dot = valid
│   weekly_audit ●  │
│   broken       ○  │  ← hollow = errors
│                   │
│  ACTIVE RUNS      │
│   code_review #4 🟢│  ← status color
│   audit #12    🟡  │
│                   │
│  SCHEDULES        │
│   weekly_audit    │
│   next: Mon 9am   │
└───────────────────┘
```

- **FLOWS**: Lists all discovered `.flow` files. Green dot = valid, hollow dot = parse/type errors. Click to navigate to Flow Library with that flow selected.
- **ACTIVE RUNS**: Lists currently running/paused flows with status color. Click to navigate directly to Run Detail.
- **SCHEDULES**: Lists configured recurring flows with next trigger time.

### 10.8 File Watcher

- Backend watches `flows_dir` (configured via `watch_dir` in `flowstate.toml` `[flows]` section) for `.flow` file changes
- On change: re-parse + type-check the file
- Push result to UI via WebSocket (`flow.file_changed`, `flow.file_error`, `flow.file_valid`)
- UI auto-updates the Flow Library and graph preview
- If the file has errors: show a persistent error banner (not a toast), preserve the last valid graph
- Technology: `watchfiles` (already a uvicorn dependency)

### 10.9 Start Run Modal

- Triggered by "Start Run" button on Flow Library page
- Shows: flow name, parameter form (auto-generated from `param` declarations in the DSL)
- Each param gets an input field with type-appropriate control (text input for `string`, number input for `number`, checkbox for `bool`)
- Default values pre-filled from DSL
- "Start" button creates the run and navigates to Run Detail

---

## 11. Lark Grammar

```lark
// Flowstate DSL Grammar

start: flow_decl

flow_decl: "flow" NAME "{" flow_body "}"

flow_body: (flow_stmt)*

flow_stmt: flow_attr
         | param_decl
         | node_decl
         | edge_decl

// Flow-level attributes
flow_attr: "budget" "=" DURATION
         | "workspace" "=" STRING
         | "on_error" "=" ERROR_POLICY
         | "context" "=" CONTEXT_MODE
         | "schedule" "=" STRING
         | "on_overlap" "=" OVERLAP_POLICY

ERROR_POLICY: "pause" | "abort" | "skip"
OVERLAP_POLICY: "skip" | "queue" | "parallel"

// Parameters
param_decl: "param" NAME ":" TYPE
          | "param" NAME ":" TYPE "=" literal

TYPE: "string" | "number" | "bool"

literal: STRING
       | NUMBER
       | "true" -> true_lit
       | "false" -> false_lit

// Nodes
node_decl: entry_node | task_node | exit_node

entry_node: "entry" NAME "{" node_body "}"
task_node:  "task"  NAME "{" node_body "}"
exit_node:  "exit"  NAME "{" node_body "}"

node_body: (node_attr)+
node_attr: "prompt" "=" string
         | "cwd" "=" STRING

// Edges
edge_decl: simple_edge | cond_edge | fork_edge | join_edge

simple_edge: NAME "->" NAME [edge_config]
cond_edge:   NAME "->" NAME "when" string [edge_config]
fork_edge:   NAME "->" "[" name_list "]"
join_edge:   "[" name_list "]" "->" NAME

name_list: NAME ("," NAME)*

edge_config: "{" edge_attr* "}"
edge_attr: "context" "=" CONTEXT_MODE
         | "delay" "=" DURATION
         | "schedule" "=" STRING

CONTEXT_MODE: "handoff" | "session" | "none"

// String literals
string: STRING | LONG_STRING

STRING: "\"" /[^"]*/ "\""
LONG_STRING: "\"\"\"" /[\s\S]*?/ "\"\"\""

// Tokens
DURATION: /[0-9]+[smh]/
NAME: /[a-zA-Z_][a-zA-Z0-9_]*/
NUMBER: /[0-9]+(\.[0-9]+)?/
COMMENT: /\/\/[^\n]*/

%import common.WS
%ignore WS
%ignore COMMENT
```

### 11.1 AST Node Definitions

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class NodeType(Enum):
    ENTRY = "entry"
    TASK = "task"
    EXIT = "exit"

class EdgeType(Enum):
    UNCONDITIONAL = "unconditional"
    CONDITIONAL = "conditional"
    FORK = "fork"
    JOIN = "join"

class ContextMode(Enum):
    HANDOFF = "handoff"
    SESSION = "session"
    NONE = "none"

class ErrorPolicy(Enum):
    PAUSE = "pause"
    ABORT = "abort"
    SKIP = "skip"

class ParamType(Enum):
    STRING = "string"
    NUMBER = "number"
    BOOL = "bool"

@dataclass
class Param:
    name: str
    type: ParamType
    default: Optional[str | float | bool] = None

@dataclass
class Node:
    name: str
    node_type: NodeType
    prompt: str
    cwd: Optional[str] = None  # per-task working directory override
    line: int = 0    # source location for error reporting
    column: int = 0

class OverlapPolicy(Enum):
    SKIP = "skip"
    QUEUE = "queue"
    PARALLEL = "parallel"

@dataclass
class EdgeConfig:
    context: Optional[ContextMode] = None  # None means "use flow default"
    delay_seconds: Optional[int] = None    # fixed delay before target starts
    schedule: Optional[str] = None         # cron expression — wait for next match

@dataclass
class Edge:
    edge_type: EdgeType
    # For unconditional/conditional: single source and target
    source: Optional[str] = None
    target: Optional[str] = None
    # For fork: single source, multiple targets
    fork_targets: Optional[list[str]] = None
    # For join: multiple sources, single target
    join_sources: Optional[list[str]] = None
    # For conditional: the when-clause
    condition: Optional[str] = None
    # Configuration
    config: EdgeConfig = field(default_factory=EdgeConfig)
    line: int = 0
    column: int = 0

@dataclass
class Flow:
    name: str
    budget_seconds: int
    on_error: ErrorPolicy
    context: ContextMode                     # flow-level default context mode
    workspace: Optional[str] = None          # optional default cwd for tasks
    schedule: Optional[str] = None           # cron expression for recurring runs
    on_overlap: OverlapPolicy = OverlapPolicy.SKIP
    params: list[Param] = field(default_factory=list)
    nodes: dict[str, Node] = field(default_factory=dict)   # name -> Node
    edges: list[Edge] = field(default_factory=list)
```

---

## 12. Error Handling

### 12.1 Parse Errors

Reported with line number, column number, and a descriptive message. The flow does not start.

```
Error at line 12, column 5: Expected '->' after node name, got 'when'
```

### 12.2 Type Check Errors

Reported with references to the offending nodes/edges and the violated rule.

```
Error [C1]: Cycle edge 'review -> test_unit' targets a node inside a
fork-join group (test_unit is part of fork from 'implement'). Cycle
targets must be outside fork-join groups.
```

### 12.3 Runtime Errors

Behavior depends on the flow's `on_error` policy:

| Policy | On task failure | On judge failure |
|--------|----------------|-----------------|
| `pause` | Pause flow. User chooses retry/skip/abort. | Always retry once, then pause. |
| `abort` | Cancel entire flow. Kill running tasks. | Always retry once, then abort. |
| `skip` | Mark task as skipped. Continue via first outgoing edge. | Always retry once, then pause (judge failures are never skipped). |

Judge failures are **never** automatically skipped because a wrong routing decision could cause cascading errors.

---

## 13. Configuration

### 13.1 `flowstate.toml`

```toml
[server]
host = "127.0.0.1"
port = 8080

[execution]
max_concurrent_tasks = 4
default_budget = "1h"

[judge]
model = "sonnet"
confidence_threshold = 0.5
max_retries = 1

[database]
# Database lives inside ~/.flowstate/ by default
path = "~/.flowstate/flowstate.db"
wal_mode = true

[flows]
watch_dir = "./flows"   # directory to watch for .flow files

[logging]
level = "info"
```

This file can be placed at `~/.flowstate/config.toml` (global) or in the current directory as `flowstate.toml` (local override).

### 13.2 CLI Interface

```bash
# Parse and validate a flow file
flowstate check myflow.flow

# Start the web server
flowstate server

# Start a flow run (also possible via web UI)
flowstate run myflow.flow --param focus="auth module"

# List runs
flowstate runs

# Show run status
flowstate status <run-id>

# List schedules
flowstate schedules

# Manually trigger a scheduled flow
flowstate trigger <flow-name>
```

---

## 14. Appendices

### Appendix A: Example Flows

#### A.1 Simple Linear Flow

```
flow setup_project {
    budget = 30m
    on_error = pause
    context = session
    workspace = "./new-project"

    entry scaffold {
        prompt = """
        Create a new Python project with:
        - pyproject.toml with Poetry
        - src/ directory structure
        - Basic test setup with pytest
        - .gitignore
        """
    }

    task add_ci {
        prompt = "Add a GitHub Actions CI pipeline that runs tests on push."
    }

    exit done {
        prompt = "Initialize a git repo and make the first commit."
    }

    scaffold -> add_ci
    add_ci -> done
}
```

#### A.2 Fork-Join Flow

```
flow full_test {
    budget = 1h
    on_error = pause
    context = handoff
    workspace = "./app"

    entry analyze {
        prompt = "Read the codebase and identify what needs testing."
    }

    task test_unit {
        prompt = "Write unit tests for all untested functions."
    }

    task test_integration {
        prompt = "Write integration tests for the API endpoints."
    }

    task test_e2e {
        prompt = "Write end-to-end tests for the critical user flows."
    }

    exit report {
        prompt = """
        Run all test suites and generate a coverage report.
        Write results to TEST_REPORT.md.
        """
    }

    analyze -> [test_unit, test_integration, test_e2e]
    [test_unit, test_integration, test_e2e] -> report
}
```

#### A.3 Flow with Cycles

```
flow iterative_refactor {
    budget = 3h
    on_error = pause
    context = handoff
    workspace = "./legacy-app"

    param target: string

    entry plan {
        prompt = """
        Analyze {{target}} and create a refactoring plan.
        Write it to REFACTOR_PLAN.md.
        """
    }

    task implement {
        prompt = """
        Pick the next item from REFACTOR_PLAN.md that hasn't been done.
        Implement the refactoring. Mark it as done in the plan.
        """
    }

    task verify {
        prompt = """
        Run all tests. Review the changes made since last verification.
        Write your assessment to REVIEW.md with a clear verdict:
        APPROVED or NEEDS_WORK.
        """
    }

    exit complete {
        prompt = "Write a summary of all refactoring done to CHANGELOG.md."
    }

    plan -> implement
    implement -> verify
    verify -> complete when "all refactoring is done and tests pass"
    verify -> implement when "more refactoring items remain or tests fail"
}
```

#### A.4 Fork-Join with Review Cycle

```
flow feature_development {
    budget = 2h
    on_error = pause
    context = handoff
    // No flow-level workspace — per-task cwd for multi-repo

    param feature: string

    entry design {
        cwd = "./backend"
        prompt = """
        Design the implementation for: {{feature}}.
        Consider both the backend (this repo) and the frontend (../frontend).
        """
    }

    task impl_backend {
        cwd = "./backend"
        prompt = "Implement the backend API changes from the design."
    }

    task impl_frontend {
        cwd = "./frontend"
        prompt = "Implement the frontend UI changes from the design."
    }

    task integrate {
        cwd = "./backend"
        prompt = """
        Run the integration test suite that exercises both
        backend and frontend together.
        """
    }

    task review {
        cwd = "./backend"
        prompt = """
        Review all changes across both repos.
        Check code quality, test coverage, and design adherence.
        """
    }

    exit ship {
        cwd = "./backend"
        prompt = "Write release notes and update documentation."
    }

    design -> [impl_backend, impl_frontend]
    [impl_backend, impl_frontend] -> integrate
    integrate -> review
    review -> ship when "code is production-ready"
    review -> design when "significant design changes needed"
}
```

#### A.5 Scheduled Deployment with Health Check

```
flow deploy_and_monitor {
    budget = 2h
    on_error = pause
    context = handoff
    workspace = "./app"

    entry prepare {
        prompt = """
        Run the full test suite. If all tests pass, build the
        deployment artifact. If tests fail, stop and report.
        """
    }

    task deploy {
        prompt = "Deploy the built artifact to the staging environment."
    }

    task check_health {
        prompt = """
        Check the staging health endpoint and run smoke tests.
        Report whether the deployment is healthy.
        """
    }

    exit done {
        prompt = "Write a deployment report with timing and health status."
    }

    // Deploy at 2am
    prepare -> deploy {
        schedule = "0 2 * * *"
    }

    // Wait 5 minutes for the deployment to stabilize
    deploy -> check_health {
        delay = 5m
    }

    check_health -> done when "deployment is healthy"
    check_health -> check_health when "still starting up" {
        delay = 2m
    }
}
```

#### A.6 Recurring Weekly Audit

```
flow weekly_audit {
    budget = 1h
    on_error = pause
    context = handoff
    workspace = "./monorepo"
    schedule = "0 9 * * MON"
    on_overlap = skip

    entry scan {
        prompt = """
        Scan all dependencies for known vulnerabilities.
        Check for outdated packages.
        """
    }

    task fix {
        prompt = "Update any vulnerable or outdated dependencies. Run tests."
    }

    exit report {
        prompt = "Write a dependency audit report to AUDIT.md."
    }

    scan -> fix
    fix -> report
}
```

### Appendix B: Risks and Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Judge returns wrong transition | High | Confidence threshold + user review on low confidence |
| Claude subprocess hangs | Medium | Per-task timeout derived from remaining budget |
| File conflicts from parallel tasks sharing cwd | High | Convention: forked tasks work on different files, or use different cwds. |
| SQLite write contention during parallel tasks | Medium | WAL mode + busy timeout |
| Budget tracking drift during pauses | Low | Only track active execution time, not paused time |
| Infinite cycle despite budget | Low | Budget guard checks after every task completion |
| Context window growth in `session` mode | Medium | Use `handoff` mode for cycles; `session` mode chains grow unboundedly |
| Agent doesn't write SUMMARY.md | Medium | Prompt engineering: instruction is injected into every task prompt. Engine warns if missing on completion. |

### Appendix C: Future Enhancements (Post-MVP)

- `otherwise` fallback edges for unmatched judge conditions
- Per-task model and tool overrides
- Nested sub-flows (a node that is itself a flow)
- Visual DSL editor in the web UI (drag-and-drop)
- Git-based workspace with per-task branches for parallel safety
- Cost tracking integration (if Claude Code exposes API costs)
- Flow templates and a library of reusable patterns
- Webhook notifications (Slack, email) on flow events
- Distributed execution across multiple machines
