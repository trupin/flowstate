# Suite 06: Cancel + Process Cleanup

**Timeout**: 5 minutes total
**Flow files needed**: `e2e_linear.flow`
**Workspace**: `/tmp/flowstate-e2e-linear` (reused)

## Purpose

Cancel a running flow and verify that:
1. The cancel API works
2. The subprocess is actually killed (no orphan `claude` processes)
3. The UI reflects the cancelled state

This is a critical reliability test — orphan subprocesses waste API credits and can interfere with future runs.

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

Watch the UI until any node shows `data-status="running"`.

### 3. Record running Claude processes

Before cancelling, record the current Claude processes:

```bash
pgrep -af "claude -p" 2>/dev/null || echo "none"
```

Note the PIDs. These are the processes we expect to be killed.

### 4. Cancel the flow via UI

Click the "Cancel" button in the run detail controls panel. **Important**: Use a specific selector to avoid matching other "Cancel" buttons (e.g., in modals):

```python
# Use the control panel's cancel button, not the modal's
cancel_btn = page.locator('[data-testid="cancel-run-btn"], .control-panel button:has-text("Cancel"), .run-controls button:has-text("Cancel")')
# Fallback: if no data-testid, use the LAST visible Cancel button (control panel renders after modal)
if cancel_btn.count() == 0:
    cancel_btn = page.locator('button:has-text("Cancel")').last
cancel_btn.click()
```

**Do NOT use** `page.locator('button:has-text("Cancel")').first` — this often matches the Start Run modal's Cancel button instead of the control panel's.

### 5. Wait for cancellation

Watch the UI until `[data-testid="flow-status"]` shows "cancelled" (max 30 seconds).

### 6. Execute process cleanup verification

Follow `procedures/process-cleanup.md`:
- Wait 5 seconds
- Check `pgrep -af "claude -p"` for orphans
- If orphans found: record as bug, kill them
- If clean: log success

### 7. Verify in UI

```python
page.goto(f"http://localhost:9090/runs/{run_id}", wait_until="networkidle")
page.screenshot(path="/tmp/flowstate-e2e-cancel-final.png", full_page=True)
```

Check:
- `[data-testid="flow-status"]` contains "cancelled"
- The previously-running task shows an appropriate terminal status

### 8. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] Flow starts and a task reaches `running`
- [ ] Cancel API returns 200
- [ ] Flow reaches `cancelled` status within 30 seconds
- [ ] No orphan `claude` processes remain after cancellation
- [ ] UI shows cancelled state
- [ ] Completed within 5-minute timeout
