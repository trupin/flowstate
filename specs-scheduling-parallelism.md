# Flowstate Spec Addendum: Task Scheduling + Flow Parallelism

This addendum extends `specs.md` with new scheduling and parallelism features.

---

## Task Scheduling

### Task Lifecycle

Tasks progress through: `scheduled` → `queued` → `running` → `completed`

- **scheduled**: task has a future `scheduled_at` timestamp; not yet in the active queue
- **queued**: task is ready to be picked up by the queue manager
- **running**: task is being processed by a flow run
- **completed/failed/cancelled**: terminal states

### Scheduling Surfaces

**1. UI / API submission:**
```
POST /api/flows/{name}/tasks
{
  "params": { ... },
  "scheduled_at": "2026-04-01T09:00:00Z",    // optional: defer to this time
  "cron": "0 9 * * 1"                          // optional: recurring (every Monday 9am)
}
```

- `scheduled_at` = null → immediate (enters queue right away)
- `scheduled_at` = timestamp → deferred (enters queue at that time)
- `cron` = expression → recurring (creates new task at each trigger)

**2. DSL: `files` edge with timing:**
```
review files bugfix after 30m          // child task enters queue in 30 minutes
review files nightly_check at "0 2 * * *"  // child task scheduled at 2am daily
```

**3. DSL: `wait` node:**
```
wait cooldown { delay = 1h }           // pause flow for 1 hour
wait market_open { until = "0 9 * * 1-5" }  // pause until 9am on weekdays
```

### Recurring Tasks

When a recurring task (with `cron`) completes, the queue manager automatically creates the next occurrence:
- Compute next trigger time from cron expression
- Create a new task with `scheduled_at` = next trigger, same params
- The recurring chain continues until the task is cancelled or the flow is disabled

---

## Flow Parallelism

### `max_parallel` (flow attribute)

Controls how many tasks from the queue can run simultaneously for this flow:

```
flow batch_job {
    max_parallel = 5    // 5 tasks can run concurrently
    ...
}
```

- Default: `1` (serial — one task at a time)
- The queue manager checks `count_running_tasks(flow_name) < max_parallel` before starting a new task
- Each flow has its own independent concurrency limit

### `wait` Node (time-based pause)

A wait node pauses the flow until a time or duration elapses:

```
wait cooldown {
    delay = 1h          // pause for 1 hour
}

wait market_open {
    until = "0 9 * * 1-5"  // pause until next weekday 9am
}
```

- Wait time does NOT count toward the flow's budget
- Uses the existing `wait_until` mechanism on task_executions
- The `DelayChecker` background loop wakes the executor when the time arrives
- Wait nodes have no `prompt` — they don't invoke Claude Code

### `fence` Node (synchronization barrier)

A fence node blocks until all running task executions in the current flow run have reached it:

```
fence sync_point {}
```

- All tasks must arrive at the fence before any can proceed past it
- Used when parallel branches (via fork-join or `max_parallel > 1`) need to synchronize
- Fence nodes have no `prompt` — they're pure synchronization primitives
- The executor marks each arriving task as `waiting`, then releases all when the last arrives

### `atomic` Node (exclusive execution)

An atomic node allows only one task execution at a time, across all concurrent runs of the same flow:

```
atomic deploy {
    prompt = "Deploy to production"
}
```

- If another run is already executing this atomic node, the current run waits
- When the running one completes, the next waiting one proceeds
- Provides mutual exclusion for operations that can't be parallelized (e.g., deployments, database migrations)
- Functions like a mutex per (flow_name, node_name)

---

## Node Types (Updated)

| Type | Keyword | Has Prompt | Purpose |
|------|---------|-----------|---------|
| `ENTRY` | `entry` | Yes | Flow entry point |
| `TASK` | `task` | Yes | Normal work node |
| `EXIT` | `exit` | Yes | Flow exit point |
| `WAIT` | `wait` | No | Time-based pause |
| `FENCE` | `fence` | No | Synchronization barrier |
| `ATOMIC` | `atomic` | Yes | Exclusive execution |

---

## Grammar Additions

```lark
// Node types
node_decl: entry_node | task_node | exit_node | wait_node | fence_node | atomic_node

wait_node: "wait" NAME "{" wait_body "}"
wait_body: wait_attr+
wait_attr: "until" "=" STRING      -> wait_until
         | "delay" "=" DURATION    -> wait_delay

fence_node: "fence" NAME "{" "}"

atomic_node: "atomic" NAME "{" node_body "}"

// Flow attributes
flow_attr: ...existing...
         | "max_parallel" "=" NUMBER -> flow_max_parallel

// Edge variants (files with timing)
edge_file_delayed: NAME "files" NAME "after" DURATION
edge_file_scheduled: NAME "files" NAME "at" STRING
```

---

## Database Schema Changes

```sql
-- Add to tasks table:
ALTER TABLE tasks ADD COLUMN scheduled_at TIMESTAMP;
ALTER TABLE tasks ADD COLUMN cron_expression TEXT;

-- New status value: 'scheduled' (deferred, not yet in active queue)
-- Update CHECK constraint to include 'scheduled'
```
