# Process Cleanup Verification

Run this after every flow cancellation to verify no orphan subprocesses remain.

## Steps

### 1. Wait for subprocess cleanup

Wait 5 seconds after the cancel API call returns. The executor should kill subprocesses during cancellation.

### 2. Check for orphan Claude processes

```bash
pgrep -af "claude -p" 2>/dev/null || echo "No claude processes found"
```

Parse the output. Look for any `claude` processes whose command line includes prompts or session IDs related to the E2E test flows (e.g., containing "flowstate-e2e", "Hello from Flowstate", or other distinctive strings from the flow prompts).

### 3. If orphans are found

For each orphan process:

1. **Record it**:
   ```
   ORPHAN PROCESS: PID {pid}
   Command: {full_command_line}
   ```

2. **Kill it**:
   ```bash
   kill {pid} 2>/dev/null || true
   sleep 2
   kill -9 {pid} 2>/dev/null || true
   ```

3. **Record as a bug**: "Cancel did not kill subprocess PID {pid}". This should be filed as an engine issue — the executor's cancel path failed to clean up the subprocess.

### 4. If no orphans

Log: `Process cleanup verified — no orphan claude processes found`

### 5. Final verification

After killing orphans (if any), run pgrep one more time to confirm they're gone:

```bash
pgrep -af "claude -p" 2>/dev/null && echo "WARNING: Processes still alive after force-kill" || echo "Clean"
```
