# Suite 16: Flow Enable/Disable Toggle

**Timeout**: 15 minutes total
**Flow files needed**: `e2e_linear.flow`
**Workspace**: auto-generated

## Purpose

Verify the flow enable/disable model: flows are enabled by default, processing tasks from their queue. Disabling a flow stops new tasks from starting (current task finishes). Re-enabling resumes queue processing.

## Procedure

### 1. Setup

Start server with `e2e_linear.flow`.

### 2. Verify flow is enabled by default

```python
resp = httpx.get(f"{BASE}/api/flows")
flow = next(f for f in resp.json() if f["name"] == "e2e_linear")
assert flow.get("enabled") is True or flow.get("enabled") is None  # default enabled
```

### 3. Submit two tasks

```python
t1 = httpx.post(f"{BASE}/api/flows/e2e_linear/tasks", json={
    "title": "Task 1", "description": "First task"
}).json()
t2 = httpx.post(f"{BASE}/api/flows/e2e_linear/tasks", json={
    "title": "Task 2", "description": "Second task"
}).json()
```

### 4. Wait for task 1 to start running

Poll `GET /api/tasks/{t1.id}` until status is "running".

### 5. Disable the flow

```python
httpx.post(f"{BASE}/api/flows/e2e_linear/disable")
```

### 6. Wait for task 1 to complete

The current task should finish even though the flow is disabled. Poll until task 1 reaches "completed".

### 7. Verify task 2 stays queued

After task 1 completes, task 2 should NOT start — the flow is disabled.

```python
t2_status = httpx.get(f"{BASE}/api/tasks/{t2.id}").json()
assert t2_status["status"] == "queued"
```

Wait 10 seconds to confirm task 2 doesn't start.

### 8. Re-enable the flow

```python
httpx.post(f"{BASE}/api/flows/e2e_linear/enable")
```

### 9. Wait for task 2 to start and complete

After re-enabling, the queue manager should pick up task 2. Poll until task 2 reaches "completed".

### 10. Verify both tasks completed

```python
t1_final = httpx.get(f"{BASE}/api/tasks/{t1.id}").json()
t2_final = httpx.get(f"{BASE}/api/tasks/{t2.id}").json()
assert t1_final["status"] == "completed"
assert t2_final["status"] == "completed"
```

## Success Criteria

- [ ] Flow is enabled by default
- [ ] Submitted tasks are processed when flow is enabled
- [ ] Disabling a flow allows the current task to finish
- [ ] Disabling prevents the next queued task from starting
- [ ] Re-enabling causes the queue manager to resume processing
- [ ] Both tasks complete after re-enable
- [ ] Completed within 15 minutes
