# Staleness Detection Procedure

Run this as a polling check while waiting for any flow to complete. Check every 30 seconds.

## Detection Logic

### 1. Query run status

```
GET http://localhost:9090/api/runs/{run_id}
```

Parse the JSON response. For each task with `"status": "running"`:

### 2. Check for staleness

A task is **stale** if ALL of the following are true:
- Its `started_at` timestamp is more than **3 minutes** ago
- Its logs show no activity in the last **2 minutes** (query `GET /api/runs/{run_id}/tasks/{task_id}/logs` and check the last entry's timestamp)
- OR it has zero log entries despite running for 3+ minutes

### 3. On stale task detected

1. **Log it clearly**:
   ```
   STALE TASK DETECTED: node "{node_name}" in run {run_id[:8]}
   Running for {wall_minutes:.0f} minutes
   Last log activity: {last_log_timestamp or "never"}
   ```

2. **Take a screenshot** for evidence:
   ```python
   page.screenshot(path=f"/tmp/flowstate-e2e-stale-{suite}-{node_name}.png", full_page=True)
   ```
   Read the screenshot to document the UI state.

3. **Cancel the run via UI**: Click the "Cancel" button in the run detail controls panel.

4. **Wait for cancellation** (max 30 seconds):
   Watch the UI until `[data-testid="flow-status"]` shows `cancelled`.

5. **Execute process cleanup** (see `procedures/process-cleanup.md`)

6. **Record as a finding**: This is a bug. Note:
   - Suite name
   - Node name
   - How long it was running
   - Last log timestamp
   - Screenshot path

### 4. Suite-level timeout

If the total wall time for the entire run exceeds the suite's timeout (specified in each suite file), even if individual tasks aren't technically stale:

1. Cancel the run
2. Execute process cleanup
3. Record as a timeout (not necessarily a bug — could be slow Claude responses)
