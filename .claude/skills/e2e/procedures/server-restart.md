# Server Restart Procedure

Run this procedure between every E2E test suite to guarantee a clean slate.

## Steps

### 1. Kill existing server processes

```bash
# Kill by PID file if it exists
if [ -f /tmp/flowstate-backend.pid ]; then
    kill $(cat /tmp/flowstate-backend.pid) 2>/dev/null || true
    rm -f /tmp/flowstate-backend.pid
fi

# Kill any remaining flowstate/uvicorn processes
pkill -f "flowstate server" 2>/dev/null || true
pkill -f "uvicorn.*flowstate" 2>/dev/null || true
pkill -f "vite.*flowstate" 2>/dev/null || true

# Wait for processes to die
sleep 2
```

### 2. Cancel orphaned DB runs

```bash
uv run python -c "
from flowstate.state.repository import FlowstateDB
import os
db_path = os.path.expanduser('~/.flowstate/flowstate.db')
if os.path.exists(db_path):
    db = FlowstateDB(db_path)
    runs = db.list_flow_runs()
    for r in runs:
        if r.status == 'running':
            db.update_flow_run_status(r.id, 'cancelled', error_message='E2E suite cleanup')
            tasks = db.list_task_executions(r.id)
            for t in tasks:
                if t.status in ('running', 'pending', 'waiting'):
                    db.update_task_status(t.id, 'failed', error_message='E2E suite cleanup')
            print(f'Cancelled orphan run {r.id[:8]}')
    db.close()
"
```

### 3. Delete the database

```bash
rm -f ~/.flowstate/flowstate.db
```

### 4. Kill orphan Claude processes

```bash
pkill -f "claude -p" 2>/dev/null || true
sleep 2
# Force-kill survivors
pkill -9 -f "claude -p" 2>/dev/null || true
```

### 5. Clean the flows directory

Remove any leftover .flow files from previous suites:

```bash
rm -f /Users/theophanerupin/code/flowstate/flows/e2e_*.flow
```

### 6. Build the UI

```bash
cd /Users/theophanerupin/code/flowstate/ui && npm run build
```

If the build fails, report the error and skip this suite.

### 7. Start the server

```bash
cd /Users/theophanerupin/code/flowstate
nohup uv run flowstate server --host 127.0.0.1 --port 9090 > /tmp/flowstate-server.log 2>&1 &
echo $! > /tmp/flowstate-backend.pid
```

### 8. Wait for server readiness

Poll `GET http://127.0.0.1:9090/api/flows` until it returns 200. Maximum wait: 15 seconds.

```bash
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:9090/api/flows > /dev/null 2>&1; then
        echo "Server ready"
        break
    fi
    sleep 0.5
done
```

If the server doesn't respond within 15s, check `/tmp/flowstate-server.log` for errors and skip this suite.

### 9. Copy suite flow files

Copy the required `.flow` files from `.claude/skills/e2e/flows/` to `./flows/`:

```bash
cp .claude/skills/e2e/flows/e2e_<suite_name>.flow flows/
```

Wait for the file watcher to discover the flow (poll `GET /api/flows` until the flow name appears, max 10s).

### 10. Create suite workspace

```bash
mkdir -p /tmp/flowstate-e2e-<suite>
rm -rf /tmp/flowstate-e2e-<suite>/*
```
