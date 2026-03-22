# [SHARED-003] Add task queue model — flows as processors, tasks as work items

## Domain
shared

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-026
- Blocks: —

## Summary
Flowstate currently has one dimension: **flows** (state machines that execute once per run). This issue adds a second dimension: **tasks** — discrete work items that are processed _by_ a flow. A flow becomes a reusable pipeline (e.g., "feature development": plan → implement → test → review → fix → done), and tasks are the items fed through it (e.g., "add user auth", "fix login bug"). Each task tracks its progress through the flow's nodes. Flows act as persistent queues: tasks are submitted, queued, and processed one at a time (or concurrently, depending on config).

Additionally, a node within a flow can **file new tasks** and route them to other flows. For example, a "review" node might file a "fix bug" task to a "bugfix" flow, creating cross-flow task chains.

**This requires proper planning before implementation.** The issue captures the high-level design; detailed sub-issues should be created for each component.

## Concept Model

```
Flow (state machine definition)
  ├── Nodes (stages in the pipeline)
  ├── Edges (transitions)
  └── Queue (pending tasks)
        ├── Task A (status: running, current_node: implement)
        ├── Task B (status: queued, position: 1)
        └── Task C (status: queued, position: 2)

Task (work item)
  ├── title: "Add user authentication"
  ├── description: "Implement OAuth2 login flow"
  ├── flow_id: "feature_dev"
  ├── status: running | queued | completed | failed | paused
  ├── current_node: "implement"  (where it is in the flow)
  ├── params: { repo: "myapp", branch: "feature/auth" }
  ├── parent_task_id: null  (or ID of task that spawned this)
  ├── created_by: "user" | "flow:review_flow/node:review"
  └── history: [{node: "plan", completed_at: ...}, ...]
```

**Key distinctions from current model:**
- **Current**: A "flow run" is a one-shot execution. You start a run, it goes through nodes, done.
- **Proposed**: A "task" is a named work item with identity. It moves through a flow's nodes. The flow is reusable — many tasks can go through the same flow. The flow has a queue of pending tasks.

## Acceptance Criteria

### Core Model
- [ ] Tasks are first-class entities with title, description, params, and lifecycle
- [ ] A flow has a task queue — tasks are submitted and processed in order
- [ ] Each task tracks its current position in the flow (current_node)
- [ ] Task history records which nodes were completed and when
- [ ] Tasks can be submitted via API (`POST /api/flows/{id}/tasks`)
- [ ] Tasks can be submitted via UI (modal with title + description + params)

### Flow as Processor
- [ ] When a task is submitted to a flow, it enters the queue
- [ ] The flow processes tasks from the queue (FIFO by default)
- [ ] Each task gets its own isolated workspace (via ENGINE-026)
- [ ] Task params are passed as flow params to each run
- [ ] The task's title and description are injected into node prompts as context

### Cross-Flow Task Filing
- [ ] A node can file a new task to another flow (via DECISION.json or a new FILE_TASK.json)
- [ ] Filed tasks include: target_flow, title, description, params, parent_task_id
- [ ] The parent task continues its own flow; the child task is queued independently
- [ ] Task lineage is tracked (parent → child relationships)

### Concurrency & Queue Management
- [ ] Configurable concurrency per flow: `max_concurrent_tasks = 1` (default: sequential)
- [ ] Queue ordering: FIFO by default, priority-based optional
- [ ] Task can be paused, cancelled, or retried independently
- [ ] Flow-level pause pauses the queue (no new tasks start, running tasks finish)

## Technical Design (High-Level)

This is a large feature requiring sub-issues. The high-level components:

### 1. Data Model (state layer)
- New `tasks` table: id, title, description, flow_id, status, current_node, params_json, parent_task_id, created_by, created_at, completed_at
- New `task_history` table: task_id, node_name, started_at, completed_at, run_id
- Modify `flow_runs` to link to a task: `task_id` foreign key (a run processes one task)
- Queue position tracking

### 2. DSL Extensions
- Optional `queue` block in flow definition:
  ```
  flow feature_dev {
      queue {
          max_concurrent = 1
          priority = fifo
      }
      ...
  }
  ```
- Task filing syntax in node prompts or via FILE_TASK.json convention

### 3. Engine Changes
- Queue manager: watches flows for pending tasks, starts runs when capacity allows
- Task-aware executor: injects task context (title, description) into node prompts
- Cross-flow task filing: node writes FILE_TASK.json, executor reads it and submits to target flow

### 4. API Changes
- `POST /api/flows/{id}/tasks` — submit a task
- `GET /api/flows/{id}/tasks` — list queued/running/completed tasks
- `GET /api/tasks/{id}` — task detail with history
- `POST /api/tasks/{id}/cancel` — cancel a queued/running task
- `GET /api/tasks` — list all tasks across flows

### 5. UI Changes
- Task submission modal (title, description, params)
- Task queue view per flow (list of pending/running/completed tasks)
- Task detail page (progress through flow, history, child tasks)
- Global task list/dashboard

### Edge Cases
- Task submitted to a flow with validation errors → reject with error
- Task submitted while flow is paused → queued but not started
- Circular task filing (A files to B which files back to A) → detect and limit depth
- Task with same title as existing → allow (tasks have unique IDs, not unique titles)
- Flow definition changes while tasks are in queue → queued tasks use the flow version at start time

## Sub-Issues to Create (during planning)

| Sub-Issue | Domain | Description |
|-----------|--------|-------------|
| STATE-007 | state | Tasks table, task_history table, queue schema |
| DSL-008 | dsl | Queue block in flow grammar |
| ENGINE-027 | engine | Queue manager + task-aware executor |
| ENGINE-028 | engine | Cross-flow task filing via FILE_TASK.json |
| SERVER-011 | server | Task API endpoints |
| UI-029 | ui | Task submission, queue view, task detail page |

## Testing Strategy
- Unit tests for task queue CRUD operations
- Unit tests for queue manager scheduling logic
- Integration test: submit task → flow processes it → task completes
- Integration test: node files child task → child task queued in target flow
- E2E: submit task via UI, watch it progress through flow nodes
