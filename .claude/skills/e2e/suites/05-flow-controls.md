# Suite 05: Flow Controls (Pause/Resume)

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_linear.flow`
**Workspace**: `/tmp/flowstate-e2e-linear` (reused)

## Purpose

Test the pause and resume control operations on a running flow. Verify the UI reflects the state transitions correctly.

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
4. The page should navigate to the Run Detail view

### 2. Wait for a task to be running

Watch the UI until any node shows `data-status="running"`. This confirms the flow is actively executing.

### 3. Pause the flow via UI

Click the "Pause" button in the run detail controls panel.

### 4. Verify pause in UI

Wait for the UI to reflect the paused state (max 30 seconds). Take a screenshot:
```python
page.screenshot(path="/tmp/flowstate-e2e-controls-paused.png", full_page=True)
```

Check:
- `[data-testid="flow-status"]` contains "paused"
- The control panel reflects paused state

### 5. Resume the flow via UI

Click the "Resume" button in the run detail controls panel.

### 6. Verify resume

Watch the UI until `[data-testid="flow-status"]` shows "running" again (max 30 seconds).

**Important note**: The executor may complete quickly after resume. If the flow goes directly from `paused` to `completed`, that's acceptable — it means resume worked and the remaining tasks finished fast.

### 8. Wait for completion

Poll with staleness detection until the flow completes or times out.

### 9. Verify final state

```python
page.goto(f"http://localhost:9090/runs/{run_id}", wait_until="networkidle")
page.screenshot(path="/tmp/flowstate-e2e-controls-final.png", full_page=True)
```

Check: flow reached `completed` status.

**If resume doesn't work** (flow stays `paused` indefinitely or errors): Record this as a bug. The mocked E2E tests noted that the executor may not fully support resume. This real test will reveal the actual behavior.

### 10. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] Flow starts and reaches `running` state
- [ ] Pause API returns 200
- [ ] Flow reaches `paused` status
- [ ] UI shows paused state
- [ ] Resume API returns 200
- [ ] Flow continues executing (or completes immediately)
- [ ] Flow reaches `completed` status
- [ ] No stale tasks detected
- [ ] Completed within 10-minute timeout

**Known risk**: Resume may not work end-to-end. If so, file an issue but don't mark the suite as failed — mark it with a note.
