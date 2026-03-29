# Flowstate Specification

Version 0.1.0 — Draft

---

## 1. Product Overview

### 1.1 What is Flowstate

Flowstate is a state-machine-based orchestration system for AI agents. It lets you define a directed graph where:

- **Nodes** are tasks executed by Claude Code subprocess sessions
- **Edges** are transitions between tasks, with routing evaluated by judge agents or task self-report
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
- Per-task model or tool overrides

---

## 2. Core Concepts

### 2.1 Flow

A named directed graph defining a workflow. Flows are **reusable processors** — they define a pipeline that processes **tasks** from a queue. A flow has:

- A **name** (identifier)
- A **budget** (wall-clock time limit)
- An optional **workspace** (default working directory for tasks; auto-generated per run if omitted)
- **Input fields** (declared via `input {}` block — template variables injected into prompts)
- Optional **output fields** (declared via `output {}` block)
- An **on_error** policy (default behavior when a task fails)
- A **context** mode (default context passing strategy)
- A **judge** mode (`true` = separate judge subprocess, `false` = task self-reports routing)
- A **harness** (which agent runtime to use — `"claude"` by default, or any ACP-compatible agent)
- A **max_parallel** limit (how many tasks from the queue can run simultaneously, default 1)
- One or more **nodes** and **edges**

Flows can be **enabled or disabled** at runtime. When disabled, the flow finishes its current task but stops processing the queue.

### 2.2 Node

A vertex in the flow graph. Six types:

| Type | Cardinality | Purpose |
|------|-------------|---------|
| `entry` | Exactly 1 | Starting point. Receives input parameters. |
| `task` | 0 or more | Intermediate work. Bulk of the flow. |
| `exit` | At least 1 | Terminal point. Flow completes when an exit node finishes. |
| `wait` | 0 or more | Time pause. Blocks the flow until a duration elapses or a cron expression matches. No Claude subprocess. |
| `fence` | 0 or more | Synchronization barrier. Blocks until all running tasks in the flow have reached the fence. |
| `atomic` | 0 or more | Exclusive execution. Only one task can execute this node at a time across all concurrent runs of the same flow. |

Entry, task, exit, and atomic nodes have a **prompt** — the instruction given to the Claude Code subprocess — and an optional **cwd**. Wait and fence nodes have no prompt (they are engine-level synchronization primitives).

### 2.3 Edge

A directed connection between nodes. Six types:

| Type | Syntax | Semantics |
|------|--------|-----------|
| Unconditional | `A -> B` | B starts when A completes. Only valid when A has exactly 1 outgoing edge. |
| Conditional | `A -> B when "condition"` | A judge (or self-report) evaluates the condition. All outgoing edges from A must be conditional. |
| Fork | `A -> [B, C]` | B and C start in parallel when A completes. |
| Join | `[B, C] -> D` | D starts when both B and C complete. The set must match a prior fork. |
| File | `A files B` | Async cross-flow filing. When A completes, a task is submitted to flow B's queue. A does not wait. |
| Await | `A awaits B` | Sync cross-flow filing. When A completes, a task is submitted to flow B and the current flow waits for it to finish. |

Edges can carry an optional **configuration block** that controls context passing and scheduling. File edges also support timing variants (`after` duration, `at` cron).

### 2.4 Task Execution

A running instance of a node. Each task execution:

- Is backed by a Claude Code subprocess with its own session (or a resumed session in `session` mode)
- Runs in the task's **cwd** (from node declaration, or inherited from flow-level `workspace`)
- Has a **generation** counter (incremented on cycle re-entry)
- Has a dedicated **task execution** record in the database with associated artifacts
- Must submit a summary artifact via the Flowstate API upon completion
- Streams output to the web UI in real time

### 2.5 Routing (Judge vs Self-Report)

When a node has conditional outgoing edges, the engine needs a routing decision. Two modes:

**Judge mode** (`judge = true`): A separate Claude Code subprocess evaluates the conditions. The judge:
1. Reads the completed task's summary artifact from the database
2. Optionally inspects the task's cwd
3. Evaluates the `when` conditions on all outgoing edges
4. Selects exactly one edge to transition through
5. Records its reasoning for auditability

Judges have **read-only** access to the task's cwd.

**Self-report mode** (`judge = false`, the default): The task agent itself decides which transition to take by submitting a `decision` artifact to the Flowstate API. The artifact contains `{"decision": "<target>", "reasoning": "...", "confidence": 0.9}`. This avoids spawning a separate subprocess for routing and is faster.

The `judge` attribute can be set at flow level (default for all nodes) or overridden per node.

### 2.6 Working Directories

Each task runs in its own **cwd** (current working directory). This is where the Claude Code subprocess executes — editing source code, running tests, etc.

**cwd resolution** (in priority order):
1. The task's `cwd` attribute (if declared in the node block)
2. The flow's `workspace` attribute (if declared)
3. Auto-generated workspace: `~/.flowstate/workspaces/<flow-name>/<run-id>/`

`workspace` is optional. If omitted, the engine auto-generates an isolated workspace directory per run.

**Worktree isolation** (`worktree = true`, the default): When the resolved workspace is a git repository, the engine creates a separate git worktree per node. Worktree references flow along edges: linear edges reuse the predecessor's worktree, fork edges create new branches, and join edges merge all branches. This provides complete file-level isolation between parallel tasks. See Section 9.7.

Tasks in the same flow can share a cwd (common for single-repo workflows) or each operate on different directories (multi-repo workflows).

**Parallel safety**: Forked tasks automatically get separate worktrees, so parallel agents cannot conflict on the same files.

### 2.7 Flowstate Data Directory

All flowstate metadata lives in **`~/.flowstate/`**, completely separated from project directories. Flowstate never writes to a project's working directory (beyond what the Claude Code agent itself does).

```
~/.flowstate/
├── flowstate.db                ← SQLite database (flows, runs, tasks, artifacts, logs)
├── config.toml                 ← global configuration (optional)
└── workspaces/                 ← auto-generated workspaces (when flow omits workspace)
    └── <flow-name>/
        └── <run-id>/
```

All task coordination data (prompts, summaries, routing decisions, cross-flow output, judge evaluations) is stored in the database via the artifact API. No per-run directories are created on disk. See Section 9.6.

### 2.8 Context Mode

Determines how context flows from one task to the next along an edge. Two modes:

| Mode | Session | Context source | Fork-join compatible |
|------|---------|---------------|---------------------|
| `handoff` | Fresh session | Previous task's summary artifact injected into prompt | Yes |
| `session` | Resumed session (`--resume`) | Full conversation history from previous task | No (linear/conditional only) |

A third option, `none`, starts a fresh session with no upstream context (only the task's own prompt).

**`handoff`** (recommended default): Each task starts a fresh Claude Code session. The previous task's summary artifact is read from the database and injected into the new task's prompt. Clean boundaries, predictable context size, works everywhere — including across different working directories and sandboxed environments.

**`session`**: The next task resumes the previous task's Claude Code session via `--resume <session_id>`. The agent retains full conversation history. Best for linear flows where deep context continuity is critical. **Not allowed on fork edges** — sessions cannot be cloned into parallel instances. Note: if the next task has a different cwd, the resumed session runs in the new cwd.

**`none`**: Fresh session with only the task's own prompt. No upstream context. Useful for tasks that are fully self-contained.

### 2.9 Budget Guard

A wall-clock time limit for a flow run. Claude Code subprocesses don't expose API costs, so time is used as a proxy.

- Tracks cumulative execution time across all tasks
- Emits warnings at 75%, 90%, and 95% of budget
- When budget is exceeded: completes the current task, then pauses the flow
- Does **not** kill tasks mid-execution

### 2.10 Generation

An integer counter per node, starting at 1. Incremented each time the node is re-entered via a cycle. Used to:

- Distinguish repeated executions of the same node
- Match fork-join groups across cycle iterations
- Provide context in the UI ("review, attempt 3")

### 2.11 Scheduling

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

This executes `monitor` every 5 minutes until the routing decision (self-report or judge) determines it's healthy.

### 2.12 Task Queue Model

Flows are **reusable processors**. Users submit **tasks** to a flow's queue. Each task carries input parameters, a title, and an optional description. The engine's **QueueManager** polls per-flow queues and processes tasks:

1. A task enters the queue with status `queued` (or `scheduled` if deferred)
2. When a slot is available (`max_parallel` not exceeded), the QueueManager picks the next task and creates a flow run
3. The flow run processes the task through the node graph
4. On completion, the task is marked `completed` with optional `output_json`

**Task scheduling**: Tasks can be submitted for immediate processing, scheduled for a specific time (`scheduled_at`), or recurring (`cron_expression`). Recurring tasks auto-create the next occurrence after completion.

**Task status lifecycle** (distinct from task_execution lifecycle in Section 6.2):

```
Scheduled ──► Queued ──► Running ──► Completed
                │            │
                │            ├──► Failed
                │            ├──► Cancelled
                │            └──► Paused
                └──► Cancelled
```

| Status | Meaning |
|--------|---------|
| `scheduled` | Deferred — `scheduled_at` is in the future |
| `queued` | Ready to be picked up by the QueueManager |
| `running` | A flow run is processing this task |
| `waiting` | Task is between nodes (e.g., at a wait/fence node) |
| `completed` | Flow run finished the exit node successfully |
| `failed` | Flow run failed or was aborted |
| `cancelled` | User cancelled the task before or during processing |
| `paused` | Flow run was paused; task resumes when the flow resumes |

**Enable/disable**: Flows can be enabled or disabled at runtime via the `flow_enabled` table. Disabled flows finish their current task but stop picking up new ones from the queue.

---

## 3. DSL Specification

### 3.1 Lexical Structure

```
Comments:       // single-line comments
Strings:        "double quoted" or """triple-quoted multiline"""
Identifiers:    [a-zA-Z_][a-zA-Z0-9_]*
Duration:       <integer>(s|m|h)  — e.g., 2h, 30m, 90s
Path:           "./relative/path" (always quoted)
Keywords:       flow, entry, task, exit, wait, fence, atomic,
                when, input, output, budget, workspace, on_error,
                context, prompt, cwd, judge, worktree, max_parallel,
                skip_permissions, harness, schedule, on_overlap, delay,
                files, awaits, after, at, subtasks, sandbox, sandbox_policy,
                lumon, lumon_config
Operators:      ->  =  [  ]  {  }  ,  :
Template vars:  {{identifier}}
```

### 3.2 Flow Declaration

```
flow <name> {
    budget = <duration>
    on_error = pause | abort | skip
    context = handoff | session | none
    workspace = <path>                    // optional — auto-generated if omitted
    judge = true | false                  // optional — default: false (self-report)
    worktree = true | false               // optional — default: true (git worktree isolation)
    max_parallel = <number>               // optional — default: 1 (serial processing)
    harness = <string>                    // optional — default: "claude" (agent runtime)
    subtasks = true | false               // optional — default: false (agent subtask management)
    skip_permissions = true | false       // optional — default: false
    sandbox = true | false                // optional — default: false (OpenShell sandbox isolation)
    sandbox_policy = <path>               // optional — path to OpenShell policy YAML
    lumon = true | false                  // optional — default: false (Lumon language-level security)
    lumon_config = <path>                 // optional — path to .lumon.json plugin contracts
    schedule = <cron_expression>          // optional — recurring flow
    on_overlap = skip | queue | parallel  // optional — default: skip

    input { <field_declarations> }        // required — declares flow input parameters
    output { <field_declarations> }       // optional — declares flow output fields

    <node_declarations>
    <edge_declarations>
}
```

`budget`, `on_error`, and `context` are required. The `input {}` block is mandatory (type check rule S9).

`workspace` is optional — if omitted, the engine auto-generates a workspace per run at `~/.flowstate/workspaces/<flow-name>/<run-id>/`.

`judge` controls the default routing mode for conditional edges. `false` (default) means task agents self-report their routing decision via the artifact API. `true` means a separate judge subprocess evaluates the conditions.

`max_parallel` controls how many tasks from the queue can run simultaneously (default 1 = serial).

`harness` sets the default agent runtime for all nodes. `"claude"` (default) uses the native Claude Code CLI. Other values (e.g., `"gemini"`, `"custom"`) use the ACP (Agent Client Protocol) to communicate with the agent subprocess. Harnesses are configured in `flowstate.toml` under `[harnesses.<name>]`. Each node can override the flow-level harness with its own `harness` attribute.

`sandbox` enables OpenShell sandbox isolation for agent subprocesses. When `true`, each task execution runs inside an isolated OpenShell container with filesystem, network, and syscall restrictions. Each task gets its own sandbox (created before execution, destroyed after). Requires `openshell` to be installed and Docker running. `sandbox_policy` specifies the path to an OpenShell policy YAML file for fine-grained control over filesystem paths, network access, and process restrictions. Each node can override the flow-level sandbox settings.

`lumon` enables Lumon language-level security for agent subprocesses (see Section 9.9). When `true`, the engine deploys Lumon's configuration into the task directory before launching the subprocess. The agent is then constrained to operate only through Lumon's safe, type-checked language primitives — it cannot run arbitrary shell commands, edit files outside the sandbox, or bypass the language's restricted vocabulary. Enforcement is layered: a `PreToolUse` hook (`sandbox-guard.py`) blocks disallowed operations at the tool level, and the deployed `CLAUDE.md` provides procedural constraints. `lumon_config` specifies the path to a `.lumon.json` file that controls plugin contracts (which operations are available and with what parameters). Each node can override the flow-level lumon settings. Requires the `lumon` package (installed from `git+https://github.com/trupin/lumon.git`).

**Important**: When using the Claude Agent SDK harness, the engine must explicitly pass the deployed settings file via `ClaudeAgentOptions(settings=<path>)` because the SDK does not automatically load `.claude/settings.json` from the working directory. The ACP harness loads it automatically from `cwd`. This is handled transparently by the engine — flow authors do not need to worry about harness differences.

`context` sets the default context mode for all edges (can be overridden per-edge). Recommended default is `handoff`.

`on_error` defines the default behavior when a task fails:

| Policy | Behavior |
|--------|----------|
| `pause` | Pause the flow. User decides via web UI (retry, skip, abort). |
| `abort` | Cancel the entire flow immediately. |
| `skip` | Mark the failed task as skipped and continue to the next edge. |

### 3.3 Input and Output Declarations

```
input {
    <name>: <type>
    <name>: <type> = <default_value>
}

output {
    <name>: <type>
}
```

Supported types: `string`, `number`, `bool`.

Input fields are referenced in prompts via `{{name}}`. They are provided when submitting a task to the flow's queue. Fields with default values are optional at submission time.

Output fields declare the structure of the flow's output (used by cross-flow `files`/`awaits` edges to map data between flows).

### 3.4 Node Declarations

```
entry <name> {
    prompt = <string>
    cwd = <path>           // optional — overrides flow-level workspace
    judge = true | false   // optional — overrides flow-level judge setting
    harness = <string>     // optional — overrides flow-level harness
    subtasks = true | false   // optional — overrides flow-level subtasks setting
    sandbox = true | false    // optional — overrides flow-level sandbox
    sandbox_policy = <path>   // optional — overrides flow-level sandbox_policy
    lumon = true | false      // optional — overrides flow-level lumon
    lumon_config = <path>     // optional — overrides flow-level lumon_config
}

task <name> {
    prompt = <string>
    cwd = <path>           // optional — overrides flow-level workspace
    judge = true | false   // optional — overrides flow-level judge setting
    harness = <string>     // optional — overrides flow-level harness
    subtasks = true | false   // optional — overrides flow-level subtasks setting
    sandbox = true | false    // optional — overrides flow-level sandbox
    sandbox_policy = <path>   // optional — overrides flow-level sandbox_policy
    lumon = true | false      // optional — overrides flow-level lumon
    lumon_config = <path>     // optional — overrides flow-level lumon_config
}

exit <name> {
    prompt = <string>
    cwd = <path>           // optional — overrides flow-level workspace
    subtasks = true | false   // optional — overrides flow-level subtasks setting
    sandbox = true | false    // optional — overrides flow-level sandbox
    sandbox_policy = <path>   // optional — overrides flow-level sandbox_policy
    lumon = true | false      // optional — overrides flow-level lumon
    lumon_config = <path>     // optional — overrides flow-level lumon_config
}

wait <name> {
    delay = <duration>     // fixed delay (e.g., 5m, 1h)
    // OR
    until = <cron>         // wait until next cron match (e.g., "0 9 * * *")
}

fence <name> { }           // synchronization barrier — no body needed

atomic <name> {
    prompt = <string>
    cwd = <path>           // optional
    harness = <string>     // optional — overrides flow-level harness
    subtasks = true | false   // optional — overrides flow-level subtasks setting
    sandbox = true | false    // optional — overrides flow-level sandbox
    sandbox_policy = <path>   // optional — overrides flow-level sandbox_policy
    lumon = true | false      // optional — overrides flow-level lumon
    lumon_config = <path>     // optional — overrides flow-level lumon_config
}
```

The prompt can use template variables (`{{field_name}}`) and triple-quoted strings for multiline content.

`cwd` sets the working directory for the Claude Code subprocess. If omitted, the task inherits the flow-level `workspace`. If neither is set, the engine auto-generates a workspace.

`wait` nodes pause the flow — they have no prompt and don't spawn a subprocess. `fence` nodes block until all running tasks in the flow reach the fence. `atomic` nodes ensure exclusive execution — only one instance runs at a time across all concurrent runs of the same flow.

### 3.5 Edge Declarations

**Unconditional** — simple sequence:
```
analyze -> implement
```

**Conditional** — routing decision (judge or self-report):
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

**File edge** — async cross-flow task filing:
```
generate_tests files code_review           // unconditional
generate_tests files code_review when "tests pass"  // conditional
generate_tests files code_review after 30m  // delayed
generate_tests files nightly at "0 2 * * *" // scheduled
```

**Await edge** — sync cross-flow task filing (blocks until the filed task completes):
```
analyze awaits deep_scan
analyze awaits deep_scan when "complex code detected"
```

File and await edges reference target flow names, not node names. The source node's `output` artifact is mapped to the target flow's declared `input` fields.

### 3.6 Context Modes

Each edge can override the flow-level `context` setting. If omitted, the flow's default applies.

| Mode | Session | What the target task receives | Restrictions |
|------|---------|------------------------------|-------------|
| `handoff` | Fresh | Previous task's summary artifact content injected into the prompt | None — works everywhere |
| `session` | Resumed | Full conversation history (continues previous task's Claude Code session) | Not allowed on fork or join edges |
| `none` | Fresh | Only the target task's own prompt | None |

**`handoff` details**: The execution engine reads the source task's `summary` artifact from the database and injects it into the target task's prompt as a "Context from previous task" section.

**`session` details**: The target task resumes the source task's Claude Code session using `--resume <session_id>`. The new task's prompt is sent as a follow-up message in the existing conversation. Context grows across the session chain.

**At join edges**: Context from all completed fork members is aggregated. Each member's `summary` artifact is injected into the join target's prompt. Session mode is not available at joins (multiple sessions cannot merge).

**Summary requirement**: Regardless of context mode, every task **must** submit a `summary` artifact via the Flowstate API. This is enforced by including curl instructions in every task prompt. The summary serves as:
- Input for the judge agent at conditional edges
- Context for downstream tasks in `handoff` mode
- Audit trail for debugging and the web UI

The task prompt includes curl examples for submitting artifacts via the API (see Section 9.6).

### 3.7 Complete Example

```
flow code_review {
    budget = 2h
    on_error = pause
    context = handoff
    workspace = "./project"

    input {
        focus: string = "all"
    }

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

    // Edges (conditional edges use self-report routing by default;
    // set judge = true at flow level to use judge subprocess instead)
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
| S6 | `entry` node has no incoming unconditional edges (conditional back-edges allowed; unconditional back-edges allowed when entry has a default edge) | Nothing transitions into the start without a judge decision |
| S7 | `exit` nodes have no outgoing edges | Nothing transitions out of a terminal |
| S8 | _(removed — workspace is auto-generated if not specified)_ | |
| S9 | Every flow must have a non-empty `input {}` block | Flows are task processors; inputs define the task contract |

### 4.2 Edge Rules

| # | Rule | Rationale |
|---|------|-----------|
| E1 | Node with 1 outgoing edge: must be unconditional | No judge needed for a single path |
| E2 | Node with 2+ outgoing edges: all must be conditional (`when`), a single fork, or exactly 1 unconditional (default) + 1+ conditional | No ambiguity in edge semantics |
| E3 | Fork and conditional edges cannot be mixed from the same node | A node is either a branch-point or a fork-point |
| E4 | Every edge references existing nodes | No dangling references |
| E5 | Fork target set must have exactly one matching join with the same node set | Every fork must close |
| E6 | Join source set must match exactly one prior fork's target set | Every join must correspond to a fork |
| E7 | `context = session` is not allowed on fork or join edges | Sessions cannot be cloned into parallel instances or merged |
| E8 | `delay` and `schedule` are mutually exclusive on an edge | An edge can wait for a duration or a cron match, not both |
| E9 | `schedule` (cron) on an edge must be a valid cron expression | Prevents runtime errors from bad cron syntax |
| E10 | `file` and `await` edge targets must reference valid flow names (validated at runtime, not parse time) | Cross-flow edges target external flows |

**Default edge pattern**: A node may have exactly one unconditional edge (the "default") plus one or more conditional edges. The engine acquires a routing decision (via judge or self-report) for the conditional edges; if no condition matches, the unconditional edge is followed as a fallback. The node is considered a conditional checkpoint for cycle analysis (C2) because routing is evaluated at that node every iteration and can exit the cycle via a conditional edge.

### 4.3 Cycle Rules

| # | Rule | Rationale |
|---|------|-----------|
| C1 | Cycle targets must be outside any fork-join group | Cycling into the middle of a fork group creates ambiguous join semantics |
| C2 | Every cycle must pass through at least one conditional edge or a node with a default edge | Prevents unconditional infinite loops — a judge must decide to re-enter |
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
2. Verify S1-S7, S9 (structural)
3. For each node, classify outgoing edges and verify E1-E10
4. Identify all fork-join pairs, verify F1-F3
5. Detect cycles via DFS, verify C1-C3
6. Verify reachability (S3-S4) via BFS from entry
```

---

## 5. Architecture

### 5.1 Component Overview

```
┌──────────────────────────────────────────────────────┐
│                  Web UI (React)                      │
│  Graph Viz │ Log Viewer │ Controls │ Task Queue UI   │
└──────────┬───────────────────────────────────────────┘
           │ WebSocket + REST
┌──────────▼───────────────────────────────────────────┐
│              Web Server (FastAPI)                     │
│    REST API  │  WebSocket Hub                        │
└──────────┬───────────────────────────────────────────┘
           │
     ┌─────┼──────────┬──────────────┬───────────────┐
     │     │          │              │               │
┌────▼───┐ ┌──▼────┐ ┌──▼──────────┐ ┌──▼────────┐ ┌──▼──────────┐
│Execution│ │Queue  │ │   State     │ │  Budget   │ │   File      │
│ Engine  │ │Manager│ │  Manager    │ │  Guard    │ │  Watcher    │
│(asyncio)│ │(polls)│ │ (SQLite)    │ │(time trk) │ │(watchfiles) │
└────┬────┘ └───────┘ └────────────┘ └───────────┘ └─────────────┘
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

- **Input**: Validated AST + parameter values + task context
- **Manages**: Claude Code subprocess lifecycle, fork-join coordination, routing (judge or self-report), cycle tracking, wait/fence/atomic synchronization, worktree isolation, cross-flow filing
- **Concurrency**: Python `asyncio` with `asyncio.create_subprocess_exec`
- **Parallelism**: Semaphore-bounded concurrent subprocesses (configurable, default 4)

### 5.4.1 Queue Manager

- **Implementation**: Background `asyncio` task polling per-flow task queues
- **Per-flow concurrency**: Reads `max_parallel` from the flow AST; only starts a new run when running count is below the limit
- **Scheduling**: Checks `scheduled_at` on tasks; transitions due tasks from `scheduled` to `queued`
- **Recurring tasks**: After a recurring task completes, auto-creates the next occurrence based on `cron_expression`
- **Enable/disable**: Checks `flow_enabled` table before processing; skips disabled flows

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
               │           │  ▲
               │           │  └── Interrupted ──► (user sends message)
               │           │
               │           └──► Failed ──► [User Decision]
               │                                    │
               └─ (delay/schedule elapsed)    ┌─────┼──────┐
                                              ▼     ▼      ▼
                                           Retry  Skip   Abort
```

| Status | Meaning |
|--------|---------|
| `pending` | Dependencies met, ready to run (or waiting for semaphore) |
| `waiting` | Delayed — a `delay` or `schedule` hasn't elapsed yet; or blocked at a fence/atomic mutex |
| `running` | Agent is executing via ACP (or wait node timer active) |
| `interrupted` | User interrupted the agent to interact — waiting for user message to resume |
| `completed` | Task finished successfully |
| `failed` | Task errored |
| `skipped` | User chose to skip a failed task |

**Interactive messaging**: While a task is `running`, users can queue messages via the API. When the agent finishes its current turn, the executor checks for unprocessed messages and re-invokes the agent with a structured prompt containing them. Users can also interrupt a running task (cancels current agent turn, sets status to `interrupted`). Resumption requires a user message. The agent cannot complete a task while unprocessed messages exist.

**Special node types**: Wait nodes go directly from `pending` → `waiting` → `completed` (no subprocess). Fence nodes go from `pending` → `waiting` (at barrier) → `completed` (when all arrive). Atomic nodes may go from `pending` → `waiting` (mutex held) → `running` → `completed`.

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

            elif has_default_edge(outgoing):  # 1 unconditional + N conditional
                default = get_default_edge(outgoing)
                conditionals = get_conditional_edges(outgoing)
                decision = await acquire_routing_decision(run, task, conditionals)
                # acquire_routing_decision: if judge=true, spawns judge subprocess;
                # if judge=false, reads decision artifact from DB
                if decision == "__none__":
                    enqueue_task(run, default.target, generation=next_gen(task),
                                edge=default)  # follow default
                else:
                    enqueue_task(run, decision.target, generation=next_gen(task),
                                edge=decision.edge)

            elif is_conditional(outgoing):
                decision = await acquire_routing_decision(run, task, outgoing)
                if decision == "__none__":
                    pause_flow(run, reason="No condition matched")
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

### 6.4 Edge Delays

When a task is created with status `waiting`:

1. The scheduler (background asyncio task) periodically checks for waiting tasks whose `wait_until` has elapsed
2. When elapsed: transition status from `waiting` to `pending`
3. The main loop picks it up as a ready task
4. Wait time does **not** count toward the flow's budget
5. The web UI shows a countdown/next-trigger time for waiting tasks

### 6.5 Recurring Flow Runs

When a flow declares `schedule`:

1. The daemon stores the schedule in the `flow_schedules` table
2. A background scheduler checks cron expressions every minute
3. On trigger:
   - Check `on_overlap` policy
   - If `skip` and a run is active: do nothing
   - If `queue`: create run with status `created`, start when previous finishes
   - If `parallel`: create and start immediately
4. The daemon emits a `flow.scheduled_trigger` event for the web UI

### 6.6 Fork-Join Execution

1. **Fork**: Source task completes → all target tasks created as `pending` simultaneously → a `fork_group` record links them
2. **Parallel execution**: Ready tasks are picked up concurrently (up to semaphore limit)
3. **Join**: Each fork member completes → check if all members of the fork group are `completed` → if yes, create pending task for the join target
4. **Generation**: All tasks in a fork group share the same generation. The join target gets `generation + 1`.

### 6.7 Conditional Branching

1. Source task completes at a node with conditional outgoing edges
2. **If judge mode** (`judge = true`):
   a. Engine reads the task's `summary` artifact from the database and spawns a **judge** subprocess
   b. Judge evaluates conditions and returns structured decision (target node + reasoning + confidence)
   c. If `confidence < 0.5`: pause flow for human review
3. **If self-report mode** (`judge = false`, default):
   a. The task prompt includes routing instructions with available transitions
   b. The task agent submits a `decision` artifact via the API with `{decision, reasoning, confidence}`
   c. Engine reads the `decision` artifact from the database after the task completes
4. If decision is `__none__`: pause flow
5. Otherwise: create pending task for the chosen target, using the edge's context mode

### 6.8 Cycle Re-entry

When a conditional edge targets an already-executed node:

1. Increment the generation counter for that node
2. Create a new `task_execution` record with the new generation
3. Context depends on the edge's mode:
   - `handoff` (default): Fresh session. The previous task's summary + judge feedback injected into prompt.
   - `session`: Resume the previous task's session. The new prompt is sent as a follow-up.
   - `none`: Fresh session with only the task's own prompt.

**Note on `session` mode and cycles**: If a cycle edge uses `session` mode, the re-entered task resumes the *source* task's session (the task that triggered the cycle), not its own previous session. This means the agent that did the review continues into the implementation, carrying its full review context.

### 6.9 Concurrency Controls

- **Max concurrent tasks**: Configurable (default 4). Enforced via `asyncio.Semaphore`.
- **Judge calls** (when `judge = true`): Count toward the concurrency limit (they are also subprocesses). In self-report mode (default), no separate judge subprocess is spawned.
- **Paused flows**: Release all semaphore slots. No subprocesses run while paused.

### 6.10 Wait Node Execution

When the engine reaches a `wait` node:

1. Create a `task_execution` with `node_type = 'wait'` and status `waiting`
2. Set `wait_until` based on the node's `delay` (now + duration) or `until` (next cron match)
3. No Claude subprocess is spawned — wait nodes are engine-level primitives
4. The scheduler checks waiting tasks periodically; when `wait_until` has elapsed, transition to `completed`
5. Wait time does **not** count toward the flow's budget
6. The UI shows a countdown timer on wait nodes

### 6.11 Fence Node Execution (Synchronization Barrier)

When a task execution reaches a `fence` node:

1. Mark the task as `waiting` at the fence
2. Check if all other running tasks for this flow run have also reached the fence (or completed)
3. If yes: release all waiting tasks — transition them to `completed` and continue to outgoing edges
4. If no: keep waiting until all arrive

This is conceptually a barrier (like `pthread_barrier`). Fence nodes are useful when `max_parallel > 1` and multiple tasks need to synchronize before continuing.

### 6.12 Atomic Node Execution (Exclusive Mutex)

Before executing an `atomic` node:

1. Check if any other task execution for this node name (across all concurrent runs of the same flow) is currently running
2. If yes: set this task to `waiting` state
3. When the running one completes: wake the next waiting one and transition it to `running`

This ensures only one instance of an atomic node executes at a time across all concurrent runs. Useful for operations that must not overlap (e.g., deploying to production).

### 6.13 Cross-Flow Filing (File and Await Edges)

When a node completes and has outgoing `file` or `await` edges:

1. Engine reads the `output` artifact from the database
2. Maps the output key-value pairs to the target flow's declared `input` fields
3. Submits a new task to the target flow's queue with the mapped parameters

**File edges** (async): The source flow continues immediately after filing. The child task runs independently.

**Await edges** (sync): The source flow blocks until the child task completes. The child task's `output_json` is available for subsequent nodes.

**Timing variants** on file edges:
- `after <duration>`: Sets `scheduled_at` on the child task instead of queuing immediately
- `at <cron>`: Sets `scheduled_at` to the next cron match

**Depth tracking**: Each filed task increments a `depth` counter (`parent.depth + 1`). This prevents unbounded recursive filing chains.

**Parent-child relationship**: The child task's `parent_task_id` references the source task, enabling lineage tracking.

### 6.14 Activity Logs

The executor emits human-readable activity log entries at key decision points. These are stored in the `task_logs` table with `log_type = 'system'` and content `{"subtype": "activity", "message": "..."}`.

Activity events include:
- `Dispatching node '<name>' (generation N)` — task subprocess about to launch
- `Edge transition: <from> → <to>` — edge traversal
- `Judge started for node '<name>'` / `Judge decided: <target>` — routing events
- `Fork started: [<targets>]` / `Fork joined at <node>` — parallel execution
- `Flow paused: <reason>` / `Flow completed` — lifecycle events

These logs appear alongside streaming Claude output in the UI log viewer, providing a unified timeline of what happened during a run.

---

## 7. Judge Protocol

> **Note**: This section describes judge mode (`judge = true`). When `judge = false` (the default), the task agent submits a `decision` artifact via the API — see Section 9.6 for the artifact protocol and Section 6.5 for the execution flow.

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
{summary artifact content for task {node_name}-{generation}, read from database}
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

See `src/flowstate/state/schema.sql` for the authoritative schema. Key tables:

- **`flow_definitions`** — Parsed DSL stored alongside source and AST JSON
- **`flow_runs`** — Execution instances, with `worktree_path` and `task_id` FK
- **`task_executions`** — Individual node runs, with `node_type` supporting `wait`, `fence`, `atomic`
- **`edge_transitions`** — Log of every edge traversal with judge decision fields
- **`fork_groups`** / **`fork_group_members`** — Track parallel execution groups
- **`task_logs`** — Streaming logs from Claude subprocesses and executor activity logs
- **`flow_schedules`** — Recurring flow runs with cron and overlap policy
- **`tasks`** — Work items submitted to flow queues (title, params, scheduling, status lifecycle)
- **`task_node_history`** — Which nodes a task passed through during execution
- **`flow_enabled`** — Runtime enable/disable toggle per flow name

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

### 9.1 Task Agent Invocation

All agent communication uses the **Agent Client Protocol (ACP)**. Flowstate connects to ACP-compatible agents as a client — no direct CLI subprocess spawning. Agents are configured in `flowstate.toml` as harnesses with a command and optional environment variables (see Section 9.8).

**Session lifecycle**:
1. **Start session**: Spawn agent subprocess, initialize ACP connection, create/load session
2. **Prompt**: Send prompt via `conn.prompt()`, stream events back to the executor
3. **Re-invoke** (optional): If user messages are queued, send another `prompt()` with the messages
4. **Interrupt** (optional): Cancel current prompt via `conn.cancel()` without killing the subprocess
5. **Kill**: Terminate the agent subprocess when the task is fully complete

Sessions are **long-lived** — the agent subprocess stays alive between `prompt()` calls, enabling multiple prompt rounds per task (for user message re-invocation) and interrupt-without-kill (for the interrupt button).

The agent is started from the task's resolved cwd (from node `cwd` attribute, or flow-level `workspace`).

**Prompt construction for `handoff` mode**:

```
You are executing a task in a Flowstate workflow.

## Context from previous task
{summary artifact content from predecessor task, read from database}

## Your task
{Node prompt with {{input_fields}} expanded}

## Working directory
Your working directory is: {resolved_cwd}

## Task coordination
When you are done, you MUST submit a summary of your work:
curl -s -X POST $FLOWSTATE_SERVER_URL/api/runs/$FLOWSTATE_RUN_ID/tasks/$FLOWSTATE_TASK_ID/artifacts/summary \
  -H "Content-Type: text/markdown" \
  -d 'Your summary here: what you did, what changed, the outcome'
```

**Prompt construction for `session` mode**:

```
## Next task: {node_name}
{Node prompt with {{input_fields}} expanded}

When you are done, submit a summary:
curl -s -X POST $FLOWSTATE_SERVER_URL/api/runs/$FLOWSTATE_RUN_ID/tasks/$FLOWSTATE_TASK_ID/artifacts/summary \
  -H "Content-Type: text/markdown" -d 'Summary of what you did and the outcome'
```

(Shorter because the full conversation context is already present in the resumed session.)

**Self-report routing appendix** (appended when `judge = false` and node has conditional outgoing edges):

```
## Routing Decision
After completing your task, decide which transition to take.

### Available Transitions
- "condition text" → transitions to: target_node
- "condition text" → transitions to: target_node
If no condition clearly matches, use "__none__".

### Submit your decision
curl -s -X POST $FLOWSTATE_SERVER_URL/api/runs/$FLOWSTATE_RUN_ID/tasks/$FLOWSTATE_TASK_ID/artifacts/decision \
  -H "Content-Type: application/json" \
  -d '{"decision": "<target_node_name>", "reasoning": "<brief explanation>", "confidence": <0.0-1.0>}'
You MUST submit this decision before completing your task.
```

**Prompt construction for join nodes** (`handoff` mode with multiple predecessors):

```
You are executing a task in a Flowstate workflow.

## Context from parallel tasks

### {fork_member_1_name}
{summary artifact content from member1, read from database}

### {fork_member_2_name}
{summary artifact content from member2, read from database}

## Your task
{Node prompt with {{input_fields}} expanded}

## Working directory and task coordination
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
| `system` | Process events, activity logs | Detect process exit, track executor decisions |

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

### 9.5 Task Setup

Before launching a task subprocess, the execution engine:

1. Creates a `task_execution` record in the database
2. Resolves the task's cwd (node `cwd` → flow `workspace` → auto-generated workspace)
3. If `worktree = true` and the workspace is a git repo, creates a git worktree for the run
4. Saves the assembled prompt as an `input` artifact in the database
5. Injects `FLOWSTATE_SERVER_URL`, `FLOWSTATE_RUN_ID`, `FLOWSTATE_TASK_ID` env vars into the agent subprocess
6. The task prompt includes curl commands for submitting artifacts via the API

### 9.6 API-Based Artifact Protocol

Agents communicate coordination data (summaries, routing decisions, cross-flow output) to the engine via the **artifact API**, not by writing files to disk. This provides a consistent protocol that works for all agents — whether running on the host or inside a sandbox.

**Environment variables**: The engine injects these into every agent subprocess:
- `FLOWSTATE_SERVER_URL` — base URL of the Flowstate server (e.g., `http://127.0.0.1:9090` for host agents, `http://host.docker.internal:9090` for sandboxed agents)
- `FLOWSTATE_RUN_ID` — the current flow run ID
- `FLOWSTATE_TASK_ID` — the current task execution ID

**Well-known artifacts:**

| Name | Content-Type | Purpose | When Required |
|------|-------------|---------|---------------|
| `summary` | `text/markdown` | What the task did, what changed, the outcome | Every task |
| `decision` | `application/json` | Self-report routing decision | Conditional edges with `judge=false` |
| `output` | `application/json` | Structured output for cross-flow filing | Nodes with `file`/`await` edges |

**Upload**: Agents submit artifacts via `POST /api/runs/{run_id}/tasks/{task_id}/artifacts/{name}`.

**`decision` artifact schema:**
```json
{
    "decision": "<target_node_name or __none__>",
    "reasoning": "Brief explanation",
    "confidence": 0.85
}
```

**`output` artifact**: Key-value pairs mapped to the target flow's declared `input` fields.

**Engine-written artifacts**: The engine also stores its own data as artifacts on the task execution — no files on disk:

| Name | Content-Type | Purpose | Written by |
|------|-------------|---------|------------|
| `input` | `text/markdown` | Assembled task prompt | Engine (at task creation) |
| `judge_request` | `text/markdown` | Judge evaluation prompt | Engine (when judge=true) |
| `judge_decision` | `application/json` | Judge's routing decision | Engine (after judge completes) |

**Engine reads**: The engine reads all artifacts from the database, never from the filesystem. Context handoff reads the predecessor's `summary` artifact. Self-report routing reads the `decision` artifact. Cross-flow filing reads the `output` artifact.

**Prompt injection**: The task prompt includes curl examples showing the agent how to submit each required artifact, using `$FLOWSTATE_SERVER_URL`, `$FLOWSTATE_RUN_ID`, and `$FLOWSTATE_TASK_ID` environment variables.

**REST API endpoints:**

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/runs/:id/tasks/:task_id/artifacts/:name` | Upload artifact content |
| `GET` | `/api/runs/:id/tasks/:task_id/artifacts/:name` | Download artifact content |
| `GET` | `/api/runs/:id/tasks/:task_id/artifacts` | List artifacts for a task |

**Database storage**: Artifacts are stored in the `task_artifacts` table with columns: `id`, `task_execution_id`, `name`, `content`, `content_type`, `created_at`. Unique constraint on `(task_execution_id, name)` — upsert semantics on duplicate.

**No per-run directories**: The `~/.flowstate/runs/` directory tree is no longer created. All coordination data lives in the database. The only filesystem directories Flowstate manages are auto-generated workspaces (`~/.flowstate/workspaces/`) and the database itself.

### 9.7 Worktree Isolation

When `worktree = true` (default) and the workspace is a git repository, each node gets its own git worktree. The worktree reference flows along edges — the graph topology maps directly to git branching and merging:

| Edge type | Git operation |
|-----------|--------------|
| **Entry node** | `git worktree add` from workspace HEAD |
| **Linear / conditional** | Next node inherits the predecessor's worktree (reuse — no copy) |
| **Fork (1 → N)** | Each branch gets `git worktree add` branching from predecessor's HEAD |
| **Join (N → 1)** | `git merge` all branch worktrees into a new worktree; conflicts left as markers for the join agent to resolve |

The worktree reference is stored as a `worktree` artifact on each task execution:
```json
{"path": "/tmp/flowstate-abc123/", "branch": "flowstate/abc123/analyze-1", "original_workspace": "/path/to/repo"}
```

**Context mode interaction**:
- `handoff`: inherits predecessor's worktree (linear) or receives merged worktree (join)
- `session`: same worktree as predecessor (reuse)
- `none`: fresh worktree from original workspace HEAD (not predecessor's)

**Auto-created workspaces**: When a flow doesn't declare `workspace`, the auto-created workspace at `~/.flowstate/workspaces/<flow>/<run>/` is initialized as a git repo (`git init` + initial commit) so worktree isolation works automatically.

**Merge conflicts at join**: When merging fork branches produces conflicts, the engine leaves conflict markers in the join worktree and adds a "Merge Conflicts" section to the join agent's prompt. The agent resolves conflicts as part of its work — it has full context from all predecessor summaries.

**Cleanup**: On run completion, all worktrees created during the run are removed and their branches deleted.

If the workspace is not a git repo and `git init` is not available, worktree isolation is silently skipped — all nodes share the raw workspace.

### 9.8 Agent Harnesses (ACP)

Flowstate supports multiple agent runtimes via the **harness** abstraction. A harness is a named agent runtime that the engine communicates with to execute nodes.

**Built-in harness**: `"claude"` (default) uses the native Claude Code CLI protocol (`--output-format stream-json`). This is the original `SubprocessManager` implementation and requires no additional configuration.

**ACP harnesses**: Any ACP-compatible agent can be used by defining a harness in `flowstate.toml`:

```toml
[harnesses.gemini]
command = ["gemini"]

[harnesses.custom_agent]
command = ["python", "my_agent.py"]
env = { MY_API_KEY = "..." }
```

The engine communicates with ACP harnesses via the Agent Client Protocol (JSON-RPC 2.0 over stdio). The lifecycle for each task:

1. Spawn agent subprocess with the configured `command`
2. ACP `initialize` — negotiate protocol version and capabilities
3. ACP `session/new` — create a session with `cwd` set to the task's workspace
4. ACP `session/prompt` — send the assembled task prompt
5. Receive `session/update` notifications (mapped to Flowstate's `StreamEvent` types)
6. On completion: `stopReason: end_turn` signals success

**Harness resolution** per node: `node.harness → flow.harness → "claude"`. This allows heterogeneous flows where different nodes use different agent runtimes:

```
flow mixed_pipeline {
    harness = "claude"        // default: Claude for most nodes

    task analyze {
        harness = "gemini"    // this node uses Gemini
        prompt = "Analyze the data"
    }

    task implement {
        prompt = "Implement changes"  // uses default (claude)
    }
}
```

**Key constraints**:
- The `"claude"` harness uses the native CLI protocol, not ACP (Claude Code doesn't support ACP yet)
- ACP is only used for non-Claude harnesses
- Judge evaluation always uses the flow's default harness
- The artifact API protocol (`summary`, `decision`, `output`) is agent-agnostic — all agents submit artifacts via HTTP POST regardless of harness

### 9.9 Lumon Security Layer

Flowstate supports **Lumon** as a language-level security layer for agent subprocesses. While `sandbox` (Section 9.8 / OpenShell) provides OS-level isolation (filesystem, network, syscall restrictions), Lumon provides **cognitive-level** constraints — the agent can only act within Lumon's safe, type-checked language primitives.

**What is Lumon**: A minimal interpreted language designed for AI agents. Safety by construction — the language provides only elementary, auditable primitives (text, list, map, io, git operations) that compose safely. Agents cannot conceive of actions outside the language's vocabulary. All code is statically type-checked before execution. Plugin contracts further restrict what operations are available and with what parameters.

**Dependency**: Flowstate installs Lumon from GitHub as a Python dependency: `lumon @ git+https://github.com/trupin/lumon.git`.

**Activation**: Set `lumon = true` at the flow level (default for all nodes) or per-node. Resolution: `node.lumon → flow.lumon → false`.

**Mechanism — three enforcement layers**:

1. **Deploy phase**: Before launching the subprocess for a `lumon=true` task, the engine runs `lumon deploy <task-dir>`. This creates:
   - `<task-dir>/CLAUDE.md` — procedural constraints telling the agent to only use `lumon --working-dir sandbox` commands
   - `<task-dir>/.claude/settings.json` — hook configuration registering `sandbox-guard.py` as a `PreToolUse` hook
   - `<task-dir>/.claude/hooks/sandbox-guard.py` — enforcement script that intercepts every tool call
   - `<task-dir>/.claude/skills/` — Lumon language reference and coding skills

2. **Hook enforcement** (`sandbox-guard.py`): Runs before every Bash, Read, Edit, and Write tool call:
   - **Bash**: Only allows `lumon --working-dir sandbox ...` commands. Rejects all other shell commands, pipe chains (`&&`, `||`, `;`), backticks, `$()`.
   - **Edit/Write**: Only allows paths inside `<task-dir>/sandbox/` or `<task-dir>/.claude/`.
   - **Read**: Only allows paths in the current directory and `~/.claude/`.
   - All decisions logged to `.claude/hooks/sandbox-guard.log`.

3. **Language constraints**: The agent writes `.lumon` code and runs it through the interpreter. Lumon's type system is checked before execution. Only safe primitives are available (`io.read`, `io.write`, `list.map`, `git.commit`, etc.). Plugin contracts in `.lumon.json` further restrict parameters (e.g., SQL queries must match a whitelist pattern, numeric arguments must be within a range).

**Settings pass-through for SDK harness**: The Claude Agent SDK does not automatically load `.claude/settings.json` from the working directory. When using the SDK harness (`"claude"` default), the engine must explicitly pass `ClaudeAgentOptions(settings=<task-dir>/.claude/settings.json)` so the CLI subprocess loads the hook configuration. The ACP harness discovers settings from `cwd` automatically — no extra handling needed.

**Plugin contracts** (`lumon_config`): An optional `.lumon.json` file controls what plugins are available and their parameter constraints. Resolved relative to the flow file's directory. Copied to `<task-dir>/.lumon.json` at deploy time. Example:

```json
{
  "plugins": {
    "db": {
      "plugin": "postgres",
      "contracts": {
        "query": "SELECT * WHERE id = *",
        "timeout": [1, 3600]
      },
      "expose": ["query"]
    }
  }
}
```

**Task output directory**: When Lumon is active, the agent operates inside `<task-dir>/sandbox/`. Since artifacts are submitted via the API (Section 9.6), there is no filesystem path difference — the agent POSTs to the same API endpoints regardless of Lumon sandboxing.

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
| Flow Library | List discovered `.flow` files from watched directory. Click to see graph preview + flow detail panel. Shows parse/type-check errors inline. "Submit Task" button opens task modal. Enable/disable toggle. **No DSL editor** — flows are edited externally and auto-discovered via file watcher. |
| Run Detail | Graph left + log viewer right + control bar. Live visualization + streaming logs + activity logs + flow controls. Active runs are accessed from the sidebar. |

### 10.2 REST API

Flows are discovered from the filesystem (watched directory), not created manually via the API.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/flows` | List discovered flows from watched directory (parsed, with error state, enabled status) |
| `GET` | `/api/flows/:id` | Get flow definition with DSL source, parse status, AST JSON |
| `POST` | `/api/flows/:id/runs` | Start a new run directly (body: params) |
| `POST` | `/api/flows/:name/enable` | Enable a flow to process its task queue |
| `POST` | `/api/flows/:name/disable` | Disable a flow |
| `POST` | `/api/flows/:name/tasks` | Submit a task to a flow's queue |
| `GET` | `/api/flows/:name/tasks` | List tasks for a flow (filterable by status) |
| `POST` | `/api/flows/:name/tasks/reorder` | Reorder queued tasks |
| `GET` | `/api/tasks` | List all tasks across all flows |
| `PATCH` | `/api/tasks/:id` | Update a queued task |
| `DELETE` | `/api/tasks/:id` | Delete a queued task |
| `GET` | `/api/runs` | List all runs (filterable by status) |
| `GET` | `/api/runs/:id` | Get run details + task executions + edges |
| `GET` | `/api/runs/:id/activity` | Get executor activity logs for a run |
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
| `POST` | `/api/runs/:id/tasks/:task_execution_id/input` | Send user input to a running task's subprocess |
| `POST` | `/api/runs/:id/tasks/:task_id/artifacts/:name` | Upload a task artifact (decision, summary, output) |
| `GET` | `/api/runs/:id/tasks/:task_id/artifacts/:name` | Download a task artifact |
| `GET` | `/api/runs/:id/tasks/:task_id/artifacts` | List artifacts for a task |
| `POST` | `/api/open` | Open a file/directory in the user's IDE |

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
| `task.interrupted` | `{task_execution_id, node_name}` | User interrupted the agent |
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
| `interrupt_task` | `{task_execution_id}` | Interrupt a running task for user interaction |
| `send_message` | `{task_execution_id, message}` | Send a user message to a running or interrupted task |

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

- **Auto-follow**: When no node is manually selected, the log viewer automatically follows the currently executing node. In parallel execution (fork-join), the first running node alphabetically is selected. Manual node clicks override auto-follow; deselecting resumes it. The auto-selected node is visually highlighted in the graph.
- Click a node in the graph → log viewer shows that task's logs
- Displays both streaming Claude output AND executor activity logs (dispatch, transition, exit events)
- Rich rendering: thinking blocks (collapsible), assistant messages (markdown), tool call blocks (expandable with input/output)
- **User input**: When a task is running, an input box at the bottom of the log viewer allows sending messages to the agent mid-execution. Sent messages appear in the log stream with a distinct "You" style. Messages are delivered via `POST /api/runs/:id/tasks/:task_execution_id/input` and stored as `task.log` events with `log_type: "user_input"`.
- "Noise" filter hides system init messages; toggle to show all
- Monospace font, dark background
- Real-time auto-scroll with pin-to-bottom toggle

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

### 10.9 Submit Task Modal

- Triggered by "Submit Task" button on Flow Library page (replaces the former "Start Run" button)
- Shows: task title (required), optional description, parameter form (auto-generated from `input {}` declarations in the DSL)
- Each input field gets a type-appropriate control (text input for `string`, number input for `number`, checkbox for `bool`)
- Default values pre-filled from DSL defaults
- Scheduling options: Immediate (default), Schedule for (datetime picker), Recurring (cron input)
- "Add to Queue" button submits the task to the flow's queue

---

## 11. Lark Grammar

See `src/flowstate/dsl/grammar.lark` for the authoritative grammar. Key additions beyond the original MVP:

- **`input {}` / `output {}` blocks** replace `param` declarations
- **`judge = true|false`**, **`worktree = true|false`**, **`skip_permissions = true|false`**, **`max_parallel = N`**, **`harness = "<name>"`**, **`subtasks = true|false`**, **`sandbox = true|false`**, **`sandbox_policy = "<path>"`**, **`lumon = true|false`**, **`lumon_config = "<path>"`** flow attributes
- **`wait`**, **`fence`**, **`atomic`** node types
- **`judge = true|false`**, **`harness = "<name>"`**, **`subtasks = true|false`**, **`sandbox = true|false`**, **`sandbox_policy = "<path>"`**, **`lumon = true|false`**, and **`lumon_config = "<path>"`** per-node overrides
- **`files`** and **`awaits`** edge types with timing variants (`after`, `at`)
- **`BOOL_LIT`** token for boolean attributes

### 11.1 AST Node Definitions

See `src/flowstate/dsl/ast.py` for the authoritative definitions. All dataclasses are frozen (immutable). Enums use `StrEnum` for JSON-friendly serialization. Key types:

- **`NodeType`**: ENTRY, TASK, EXIT, WAIT, FENCE, ATOMIC
- **`EdgeType`**: UNCONDITIONAL, CONDITIONAL, FORK, JOIN, FILE, AWAIT
- **`ContextMode`**: HANDOFF, SESSION, NONE
- **`ErrorPolicy`**: PAUSE, ABORT, SKIP
- **`TaskTypeField`**: Represents a single field in `input {}` or `output {}` (name, type, optional default)
- **`Node`**: name, node_type, prompt, cwd, judge (per-node override), harness (per-node override), subtasks (per-node override), sandbox (per-node override), sandbox_policy (per-node override), lumon (per-node override), lumon_config (per-node override), wait_delay_seconds, wait_until_cron
- **`EdgeConfig`**: context override, delay_seconds, schedule (cron)
- **`Edge`**: edge_type, source, target, fork_targets (tuple), join_sources (tuple), condition, config
- **`Flow`**: name, budget_seconds, on_error, context, workspace, schedule, on_overlap, skip_permissions, judge (default False), harness (default "claude"), subtasks (default False), sandbox (default False), sandbox_policy (default None), lumon (default False), lumon_config (default None), worktree (default True), input_fields (tuple of TaskTypeField), output_fields, max_parallel (default 1), nodes (dict), edges (tuple)

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
port = 9090

[execution]
max_concurrent_tasks = 4
default_budget = "1h"
worktree_cleanup = true         # clean up git worktrees after run completes

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

# Optional: define additional agent harnesses (ACP protocol)
# [harnesses.gemini]
# command = ["gemini"]
# env = { GEMINI_API_KEY = "..." }

[logging]
level = "info"
```

This file can be placed at `~/.flowstate/config.toml` (global) or in the current directory as `flowstate.toml` (local override).

### 13.2 CLI Interface

```bash
# Parse and validate a flow file
flowstate check myflow.flow

# Start the web server (default port 9090)
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

## 14. Agent Subtask Management

### 14.1 Overview

Agents executing in nodes can optionally have access to a **subtask management system**. When enabled, the engine injects API instructions into the agent's prompt so it can create, list, and update subtasks tracked in the Flowstate database. Subtasks are visible in the UI for real-time progress monitoring.

This is an opt-in feature controlled by the `subtasks` attribute at the flow and node level.

### 14.2 DSL Attribute

```
flow my_flow {
    subtasks = true | false    // optional — default: false
    ...

    task my_task {
        subtasks = true | false  // optional — overrides flow-level default
        ...
    }
}
```

Inheritance: `node.subtasks → flow.subtasks → false`. Same pattern as `judge`.

Only entry, task, exit, and atomic nodes can have `subtasks` (they spawn subprocesses). Wait and fence nodes cannot.

### 14.3 Subtask Data Model

Each subtask belongs to a `task_execution` and has:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `task_execution_id` | UUID | Parent task execution |
| `title` | string | Human-readable description |
| `status` | enum | `todo`, `in_progress`, `done` |
| `created_at` | ISO 8601 | Creation timestamp |
| `updated_at` | ISO 8601 | Last update timestamp |

Subtasks persist after the parent task completes. They are available for auditing and can be queried by downstream agents.

### 14.4 REST API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/runs/{run_id}/tasks/{task_execution_id}/subtasks` | Create a subtask. Body: `{"title": "..."}`. Returns 201. |
| `GET` | `/api/runs/{run_id}/tasks/{task_execution_id}/subtasks` | List subtasks for a task execution. Returns array. |
| `PATCH` | `/api/runs/{run_id}/tasks/{task_execution_id}/subtasks/{subtask_id}` | Update subtask status. Body: `{"status": "done"}`. Returns 200. |

### 14.5 Prompt Injection

When `subtasks` is enabled for a node, the engine appends a "Task Management" section to the agent's prompt containing:

1. The Flowstate server base URL
2. The current task execution ID and run ID
3. Curl examples for creating, listing, and updating subtasks
4. In handoff mode: the predecessor's task execution ID for introspection

### 14.6 Context Passing

Subtask data is **not** included in the summary artifact or passed automatically to successor nodes. Instead, the handoff prompt includes the predecessor's `task_execution_id`, allowing downstream agents to query the predecessor's subtasks via the API if needed.

### 14.7 WebSocket Events

A `SUBTASK_UPDATED` event is emitted when a subtask is created or updated. Payload includes the full subtask data and `flow_run_id` for client-side filtering.

---

## 15. Appendices

### Appendix A: Example Flows

> All examples below use self-report routing (`judge = false`, the default). The task agent at each conditional node submits a `decision` artifact via the API to choose the next transition. Add `judge = true` to the flow declaration to use a separate judge subprocess instead.

#### A.1 Simple Linear Flow

```
flow setup_project {
    budget = 30m
    on_error = pause
    context = session
    workspace = "./new-project"

    input {
        description: string
    }

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

    input {
        description: string
    }

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

    input {
        target: string
    }

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

    input {
        feature: string
    }

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

    input {
        description: string
    }

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

    input {
        description: string = "Weekly dependency audit"
    }

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
| Agent doesn't submit summary artifact | Medium | Prompt engineering: curl instruction is injected into every task prompt. Engine warns if missing on completion. |
| Worktree creation fails (not a git repo) | Low | Engine silently skips worktree creation; tasks run in the original workspace. Logged as warning. |
| Task queue starvation (disabled flow with queued tasks) | Low | UI shows disabled status clearly. Tasks remain queued and resume when flow is re-enabled. |
| Cross-flow circular filing (A files B files A) | Medium | Depth counter on tasks prevents unbounded recursion. Engine should enforce a max depth limit. |
| Self-report agent submits wrong decision | Medium | Same mitigation as judge: confidence threshold + user review on low confidence. |

### Appendix C: Future Enhancements (Post-MVP)

- Per-task model and tool overrides
- Nested sub-flows (a node that is itself a flow)
- Visual DSL editor in the web UI (drag-and-drop)
- Cost tracking integration (if Claude Code exposes API costs)
- Flow templates and a library of reusable patterns
- Webhook notifications (Slack, email) on flow events
- Distributed execution across multiple machines
