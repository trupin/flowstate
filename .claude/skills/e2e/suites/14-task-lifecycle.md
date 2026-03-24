# Suite 14: Task Queue Lifecycle

**Timeout**: 20 minutes total
**Flow files needed**: `e2e_dev_pipeline.flow`
**Workspace**: auto-generated (no workspace declared)

## Purpose

Verify the full task queue lifecycle: submit tasks via API, queue manager picks them up, tasks progress through flow nodes, and tasks complete with correct history. Tests sequential processing (max_concurrent=1).

## Procedure

### 1. Setup

Start server with `e2e_dev_pipeline.flow`. This flow has a `task` block with typed input/output.

### 2. Submit first task via API

```python
import httpx
resp = httpx.post(f"{BASE}/api/flows/e2e_dev_pipeline/tasks", json={
    "title": "Add greeting utility",
    "description": "Create a Python function that returns a greeting message for a given name",
})
task1_id = resp.json()["id"]
```

Verify: status 201, task status is "queued".

### 3. Submit second task

```python
resp = httpx.post(f"{BASE}/api/flows/e2e_dev_pipeline/tasks", json={
    "title": "Add farewell utility",
    "description": "Create a Python function that returns a farewell message",
})
task2_id = resp.json()["id"]
```

### 4. Wait for first task to start

Poll `GET /api/tasks/{task1_id}` until status is "running". The queue manager should pick it up within a few seconds.

### 5. Verify second task is still queued

While task1 is running, `GET /api/tasks/{task2_id}` should show status "queued".

### 6. Monitor task1 progress

Poll task1 and track `current_node` changes: plan → implement → verify → done.

### 7. Wait for task1 completion

Poll until task1 status is "completed". Verify:
- `flow_run_id` is set
- `current_node` is the exit node
- `completed_at` is set

### 8. Check task1 history

```python
resp = httpx.get(f"{BASE}/api/tasks/{task1_id}")
history = resp.json()["history"]
```

Verify 4 entries (plan, implement, verify, done), each with started_at and completed_at.

### 9. Wait for task2 to auto-start

After task1 completes, the queue manager should start task2. Poll until task2 status is "running".

### 10. Wait for task2 completion

Poll until task2 completes.

### 11. Verify separate workspaces

Check that task1 and task2 have different `flow_run_id` values.

### 12. Launch Playwright and verify UI

Open the flow library page. Check:
- TaskQueuePanel shows both completed tasks
- Click a task → shows detail

## Success Criteria

- [ ] Two tasks submitted and processed sequentially (FIFO)
- [ ] Task status transitions: queued → running → completed
- [ ] current_node updates as each node executes
- [ ] Task history with timestamps for all 4 nodes
- [ ] Second task auto-starts after first completes
- [ ] Separate flow runs per task
- [ ] No stale tasks, no orphan processes
- [ ] Completed within 20 minutes
