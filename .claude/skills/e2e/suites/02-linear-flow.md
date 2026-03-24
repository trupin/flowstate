# Suite 02: Linear Flow

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_linear.flow`
**Workspace**: `/tmp/flowstate-e2e-linear`

## Purpose

Start a simple 3-node linear flow (start → process → done) and verify it completes end-to-end with real Claude Code. This is the core test — a real agent creates files, another reads and transforms them, and the exit node verifies the result.

## Procedure

### 1. Launch Playwright

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

### 2. Start the flow via UI

1. Click the `e2e_linear` flow in the sidebar: `page.locator('[data-testid="sidebar-flow-e2e_linear"]').click()`
2. Click the "Start Run" button: look for a button with text "Start Run" or `[data-testid="start-run-button"]`
3. In the Start Run modal, click Submit/Start (no parameters needed for this flow)
4. The page should navigate to the Run Detail view (`/runs/{id}`)

**Do NOT fall back to the API.** If the UI-based start doesn't work, record it as a bug and fail the suite.

### 3. Monitor execution with staleness detection

Start a polling loop (check every 15 seconds):

1. Query `GET http://localhost:9090/api/runs/{run_id}` for current status
2. Log the current state: run status, which tasks are pending/running/completed
3. Take a screenshot at each state transition (node entering "running")
4. Run staleness detection per `procedures/staleness-detection.md`

### 4. Wait for completion

Poll until `run.status == "completed"` or until the 10-minute timeout.

If timeout: cancel the run, run process cleanup, record as timeout.

### 5. Verify graph auto-update (UI-021)

**Critical**: Do NOT reload the page after detecting completion via API. The graph should have updated automatically via WebSocket events. Wait 3 seconds for the WebSocket `flow.completed` event to propagate, then verify the graph in its current state:

```python
page.wait_for_timeout(3000)
page.screenshot(path="/tmp/flowstate-e2e-linear-autoupdate.png", full_page=True)
```

Check that all nodes show completed status without a page reload. This verifies UI-021 (graph auto-update on completion).

Then reload to verify consistency:

```python
page.goto(f"http://localhost:9090/runs/{run_id}", wait_until="networkidle")
```

Check:
- `[data-testid="node-start"][data-status="completed"]` is visible
- `[data-testid="node-process"][data-status="completed"]` is visible
- `[data-testid="node-done"][data-status="completed"]` is visible
- `[data-testid="flow-status"]` contains text "completed"

### 6. Verify log viewer

Click on the "start" node. The log viewer (`[data-testid="log-viewer"]`) should contain log entries (not empty).

Click on the "process" node. The log viewer should update to show different content.

### 7. Verify output files

```bash
test -f /tmp/flowstate-e2e-linear/hello.txt && echo "hello.txt exists" || echo "hello.txt MISSING"
test -f /tmp/flowstate-e2e-linear/result.txt && echo "result.txt exists" || echo "result.txt MISSING"
```

### 8. Take final screenshot

```python
page.screenshot(path="/tmp/flowstate-e2e-linear-final.png", full_page=True)
```

### 9. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] Flow starts successfully via UI
- [ ] All 3 nodes reach `completed` status
- [ ] Run status is `completed`
- [ ] Graph auto-updated on completion without page reload (UI-021)
- [ ] Log viewer shows entries for each node
- [ ] Output files exist on disk (`hello.txt`, `result.txt`)
- [ ] No stale tasks detected
- [ ] Completed within 10-minute timeout
