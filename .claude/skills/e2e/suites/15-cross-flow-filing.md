# Suite 15: Cross-Flow Task Filing (files edge)

**Timeout**: 15 minutes total
**Flow files needed**: `e2e_review_pipeline.flow`, `e2e_bugfix_pipeline.flow`
**Workspace**: auto-generated

## Purpose

Verify that `files` edges (async cross-flow task filing) work end-to-end. When a node with a `files` edge completes, a child task is automatically created in the target flow's queue and processed independently.

## Procedure

### 1. Setup

Start server with both `e2e_review_pipeline.flow` and `e2e_bugfix_pipeline.flow`.

### 2. Submit parent task

```python
resp = httpx.post(f"{BASE}/api/flows/e2e_review_pipeline/tasks", json={
    "title": "Review code for bugs",
    "description": "Analyze the codebase for common bug patterns",
})
parent_id = resp.json()["id"]
```

### 3. Wait for parent task to start and progress

Poll until `current_node` reaches "review" (the node with the `files` edge).

### 4. Wait for review node to complete

Poll until parent task's `current_node` moves past "review" (to "report" or task completes).

### 5. Check for child task

After the review node completes, a child task should appear in `e2e_bugfix_pipeline`:

```python
resp = httpx.get(f"{BASE}/api/flows/e2e_bugfix_pipeline/tasks")
children = resp.json()
```

Verify at least one child task exists with:
- `parent_task_id == parent_id`
- `created_by` contains "flow:e2e_review_pipeline/node:review"
- `flow_name == "e2e_bugfix_pipeline"`

### 6. Wait for child task to be processed

The queue manager should pick up the child task and process it through the bugfix pipeline. Poll until child task reaches "completed".

### 7. Verify parent-child lineage

```python
resp = httpx.get(f"{BASE}/api/tasks/{parent_id}")
detail = resp.json()
assert len(detail["children"]) >= 1
child = detail["children"][0]
assert child["flow_name"] == "e2e_bugfix_pipeline"
```

### 8. Wait for parent task to complete

The parent should continue to the "report" exit node and complete independently of the child.

### 9. Verify both tasks completed

Both parent and child should have status "completed".

## Success Criteria

- [ ] Parent task processes through review pipeline
- [ ] `files` edge auto-creates child task in bugfix pipeline queue
- [ ] Child task has correct parent_task_id and created_by
- [ ] Child task queued and auto-processed by queue manager
- [ ] Parent-child lineage visible in API detail response
- [ ] Parent flow continues to completion (not blocked by child)
- [ ] Both tasks complete successfully
- [ ] Completed within 15 minutes
