---
description: Manage the Flowstate dev server — start, stop, debug, logs, status
user_invocable: true
---

Manage the Flowstate development server. Parse the subcommand from the user's arguments.

## Subcommands

### `start` (default if no subcommand given)

Start the Flowstate backend server and optionally the frontend dev server.

Parse flags from the user's arguments:
- `--frontend` or `--ui` → also start the Vite dev server for the React UI
- `--port <N>` → override the backend port (default: 9090)
- `--watch-dir <path>` → override the flow watch directory (default: `./flows`)
- `--build` → build the UI before starting (for production-like mode with static serving)

**Backend only** (default):
```bash
uv run flowstate server --host 127.0.0.1 --port 9090
```

**With frontend dev server** (`--frontend`):
Start both processes in background, report their PIDs.
```bash
# Terminal 1: Backend
uv run flowstate server --host 127.0.0.1 --port 9090 &
BACKEND_PID=$!

# Terminal 2: Vite dev server (proxies /api and /ws to backend)
cd ui && npm run dev &
FRONTEND_PID=$!

echo "Backend PID: $BACKEND_PID (port 8080)"
echo "Frontend PID: $FRONTEND_PID (port 5173)"
echo "Open http://localhost:5173 in your browser"
```

**With build** (`--build`):
```bash
cd ui && npm run build
uv run flowstate server --host 127.0.0.1 --port 9090
# UI served at http://localhost:9090 via static file serving
```

Save PIDs to `/tmp/flowstate-backend.pid` and `/tmp/flowstate-frontend.pid` so `/server stop` can find them.

### `stop`

Stop running Flowstate server processes and cancel any in-flight flow runs.

1. **Cancel orphaned flow runs first** — the server tracks executors in-memory, so after a stop/restart they're lost. Mark any `running` flow runs as `cancelled` and their `running`/`pending`/`waiting` tasks as `failed` in the DB:
   ```bash
   uv run python -c "
   from flowstate.state.repository import FlowstateDB
   import os
   db = FlowstateDB(os.path.expanduser('~/.flowstate/flowstate.db'))
   runs = db.list_flow_runs()
   for r in runs:
       if r.status == 'running':
           db.update_flow_run_status(r.id, 'cancelled', error_message='Cancelled on server stop')
           tasks = db.list_task_executions(r.id)
           for t in tasks:
               if t.status in ('running', 'pending', 'waiting'):
                   db.update_task_status(t.id, 'failed', error_message='Run cancelled on server stop')
           print(f'Cancelled run {r.id[:8]}')
   db.close()
   "
   ```
2. Read PIDs from `/tmp/flowstate-backend.pid` and `/tmp/flowstate-frontend.pid`
3. Kill each process if still running
4. Clean up PID files
5. If no PID files exist, search for processes:
   ```bash
   # Find uvicorn/flowstate processes
   pgrep -f "flowstate server" || pgrep -f "uvicorn.*flowstate"
   # Find vite dev server
   pgrep -f "vite.*flowstate-ui"
   ```
6. Report which processes were stopped

### `status`

Check if the server is running and report its state.

1. Check PID files and whether processes are alive
2. Try to reach the health endpoint:
   ```bash
   curl -s http://localhost:9090/api/flows 2>/dev/null
   ```
3. Check if Vite dev server is running:
   ```bash
   curl -s http://localhost:5173 2>/dev/null
   ```
4. Report: backend up/down, frontend up/down, port, watch directory

### `logs`

Show recent server logs or tail them live.

Parse flags:
- No flags → show last 50 lines of server output
- `--follow` or `-f` → tail logs in real-time
- `--errors` → filter to error/warning lines only

If the server was started via this skill, logs go to `/tmp/flowstate-server.log`. If not, suggest starting with `/server start` or checking the terminal where the server was started.

```bash
# Last 50 lines
tail -50 /tmp/flowstate-server.log

# Follow
tail -f /tmp/flowstate-server.log

# Errors only
grep -iE "(error|warning|traceback|exception)" /tmp/flowstate-server.log | tail -50
```

### `debug`

Debug the Flowstate application — server-side issues, API behavior, or UI rendering.

Parse what the user wants to debug from their message. There are four modes:

#### Stuck agent detection (always check during debug)
**IMPORTANT**: Always check for tasks that have been running for more than 5 minutes with no recent log output. These are likely stuck — the subprocess exited without the executor detecting it.

```bash
# Check for stuck tasks
curl -s http://localhost:9090/api/runs 2>&1 | python3 -c "
import sys,json,urllib.request
from datetime import datetime, timezone
runs = json.load(sys.stdin)
now = datetime.now(timezone.utc)
for r in runs:
    if r['status'] != 'running': continue
    resp = urllib.request.urlopen(f'http://localhost:9090/api/runs/{r[\"id\"]}')
    detail = json.loads(resp.read())
    for t in detail['tasks']:
        if t['status'] != 'running' or not t.get('started_at'): continue
        started = datetime.fromisoformat(t['started_at'])
        wall_mins = (now - started).total_seconds() / 60
        if wall_mins > 5:
            # Check last log activity
            log_resp = urllib.request.urlopen(f'http://localhost:9090/api/runs/{r[\"id\"]}/tasks/{t[\"id\"]}/logs')
            logs = json.loads(log_resp.read())['logs']
            last_log = logs[-1]['timestamp'] if logs else 'never'
            print(f'STUCK: {t[\"node_name\"]} in run {r[\"id\"][:8]} — running {wall_mins:.0f}min, last log: {last_log}')
"
```

If a task is stuck (running >5min with no recent logs), report it and suggest cancelling the run. The root cause is that Claude Code subprocesses sometimes exit without the executor's stream reader detecting EOF.

#### API debugging (default if no specific target)
Test API endpoints and inspect responses:
```bash
# List flows
curl -s http://localhost:9090/api/flows | python -m json.tool

# List runs
curl -s http://localhost:9090/api/runs | python -m json.tool

# Get specific run
curl -s http://localhost:9090/api/runs/<run_id> | python -m json.tool

# Check flow file parsing
uv run flowstate check <path_to_flow_file>
```

Try the relevant endpoints based on what the user describes. Inspect response codes, error messages, and data shapes. Cross-reference with `specs.md` Section 10 if the API behavior seems wrong.

#### Server-side debugging
If the user reports backend errors:
1. Check server logs: `tail -100 /tmp/flowstate-server.log`
2. Check database state:
   ```bash
   uv run python -c "
   from flowstate.state.repository import FlowstateDB
   db = FlowstateDB('~/.flowstate/flowstate.db')
   runs = db.list_flow_runs()
   for r in runs: print(f'{r.id[:8]}... {r.status} {r.created_at}')
   db.close()
   "
   ```
3. Check for Python exceptions in logs
4. Verify the flow file is valid: `uv run flowstate check <path>`
5. Check config: `cat flowstate.toml 2>/dev/null || echo "No local config"`

#### UI debugging (Playwright)
If the user reports frontend/UI issues, use Playwright to inspect the page:
```bash
uv run python -c "
from playwright.sync_api import sync_playwright
import json, sys

url = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:5173'

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, wait_until='networkidle')

    # Capture console errors
    errors = []
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)

    # Screenshot
    page.screenshot(path='/tmp/flowstate-debug.png', full_page=True)
    print(f'Screenshot saved to /tmp/flowstate-debug.png')

    # Check for React error boundaries
    error_el = page.query_selector('[data-testid=\"error-banner\"]')
    if error_el:
        print(f'Error banner visible: {error_el.inner_text()}')

    # Check sidebar state
    sidebar = page.query_selector('.sidebar')
    if sidebar:
        flows = page.query_selector_all('[data-testid^=\"sidebar-flow-\"]')
        print(f'Sidebar shows {len(flows)} flows')

    # Check graph nodes
    nodes = page.query_selector_all('[data-testid^=\"node-\"]')
    if nodes:
        for n in nodes:
            name = n.get_attribute('data-testid').replace('node-', '')
            status = n.get_attribute('data-status')
            print(f'  Node {name}: {status}')

    # Report console errors
    if errors:
        print(f'Console errors: {len(errors)}')
        for e in errors[:5]:
            print(f'  {e}')

    # Network requests that failed
    page.reload(wait_until='networkidle')

    browser.close()
" "$URL"
```

Replace `$URL` with the URL the user provides, or default to `http://localhost:5173` (Vite dev) or `http://localhost:9090` (production).

Then read the screenshot at `/tmp/flowstate-debug.png` to see what the page looks like. Report findings: console errors, visible error banners, sidebar state, graph node statuses, failed network requests.

### `restart`

Shorthand for `stop` followed by `start` with the same flags. **IMPORTANT**: The `stop` step must cancel orphaned flow runs in the DB before killing the server process — see `stop` subcommand for details.

## Hot reload after UI changes

When you modify UI files (`ui/src/**/*.tsx`, `*.css`, `*.ts`), you MUST reload the browser to see changes. Follow this procedure:

1. Build the UI: `cd /Users/theophanerupin/code/flowstate/ui && npm run build`
2. Use Playwright to reload the user's browser page:
```bash
uv run python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # visible browser
    ctx = browser.new_context()
    # Connect to existing page or open new one
    page = ctx.new_page()
    page.goto('http://localhost:9090', wait_until='networkidle')
    print('Page reloaded with latest build')
    # Don't close — leave browser open for the user
    input('Press Enter to close...')
"
```

**Shortcut**: If the server is running in `--build` mode (static serving), just rebuild and the next page load picks up changes automatically. No server restart needed — the static files are served directly from `ui/dist/`.

If you changed **backend Python files**, you MUST restart the server (`/server restart`). The UI build is separate from the backend.

## Notes

- The backend serves on port 9090 by default. The Vite dev server serves on port 5173 and proxies API/WS calls to 9090.
- For development, use `--frontend` to get hot-reload on UI changes.
- For production-like testing, use `--build` to serve the built UI from the backend directly.
- The `debug` subcommand with Playwright requires `uv sync --group e2e` and `uv run playwright install chromium`.
- **IMPORTANT**: Always start the server from the project root directory (`/Users/theophanerupin/code/flowstate/`), not from `ui/` or any subdirectory. The `watch_dir` is `./flows` which is relative to CWD.
