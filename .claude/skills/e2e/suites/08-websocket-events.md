# Suite 08: WebSocket Real-Time Events

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_linear.flow`
**Workspace**: `/tmp/flowstate-e2e-linear` (reused)

## Purpose

Verify that real-time log streaming works during flow execution. When a task is running and selected in the UI, the log viewer should show live output from the Claude Code subprocess.

## Procedure

### 1. Launch Playwright and start the flow

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.goto("http://localhost:9090", wait_until="networkidle")
```

Start the flow via UI:
1. Click `e2e_linear` in the sidebar
2. Click "Start Run" button
3. In the modal, click Submit/Start (no parameters needed)
4. The page should navigate to the Run Detail view immediately

### 2. Wait for a node to be running

Watch the UI until any node shows `data-status="running"`. Reload the page if needed to get the live state.

### 3. Click the running node

Find and click the running node:
```python
running_node = page.locator('[data-status="running"]').first
running_node.click()
```

### 4. Verify log streaming

After clicking the running node, observe the log viewer:

```python
log_viewer = page.locator('[data-testid="log-viewer"]')
```

Wait for at least one log entry to appear (max 60 seconds). Claude Code may take time to produce output.

Take a screenshot showing the live log streaming:
```python
page.screenshot(path="/tmp/flowstate-e2e-websocket-streaming.png", full_page=True)
```

Read the screenshot to confirm logs are actually rendering (not empty, not an error state).

### 5. Wait for flow to complete

Continue polling with staleness detection. The flow should complete within the timeout.

### 6. Verify log viewer with completed nodes

After completion, reload and test clicking different nodes:

```python
page.goto(f"http://localhost:9090/runs/{run_id}", wait_until="networkidle")
```

Click `start` node → verify log viewer shows content.
Click `process` node → verify log viewer shows different content.
Click `done` node → verify log viewer shows different content.

Each node should have non-empty log content visible in the log viewer.

### 7. Take final screenshot

```python
page.screenshot(path="/tmp/flowstate-e2e-websocket-final.png", full_page=True)
```

### 8. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] Flow starts and runs with at least one task in `running` state
- [ ] Log viewer shows entries while a task is running (real-time streaming)
- [ ] Clicking different completed nodes changes the log viewer content
- [ ] Each node has non-empty logs visible
- [ ] Flow completes successfully
- [ ] No stale tasks detected
- [ ] Completed within 10-minute timeout
