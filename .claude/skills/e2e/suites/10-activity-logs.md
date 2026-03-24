# Suite 10: Activity Logs in UI Console

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_linear.flow`
**Workspace**: `/tmp/flowstate-e2e-linear` (reused)

## Purpose

Verify that executor activity logs (ENGINE-024) appear in the UI's log viewer alongside task logs. Activity logs are human-readable messages emitted at key orchestrator decision points: node dispatch, edge transitions, and other executor-level events.

## Procedure

### 1. Launch Playwright and start the flow

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.goto("http://localhost:9090", wait_until="networkidle")
```

Start the flow via UI:
1. Click `e2e_linear` in the sidebar
2. Click "Start Run" button
3. In the modal, click Submit/Start
4. The page should navigate to the Run Detail view

### 2. Wait for flow to complete

Poll `GET /api/runs/{run_id}` until `status == "completed"` or timeout.

### 3. Verify activity logs in the log viewer

After flow completes, reload the run detail page and click on the first task node (e.g., `start`).

Check the log viewer for activity log entries. Activity logs render with the `.log-activity` CSS class and contain orchestrator decision messages with unicode prefixes:

- `▶` — Node dispatch ("Dispatching node 'start'")
- `→` — Edge transition ("Edge transition: start → process")

Look for these in the DOM:
```python
activity_entries = page.locator('.log-activity')
activity_count = activity_entries.count()
```

If no `.log-activity` entries are found, try checking the raw log content:
```python
log_viewer = page.locator('[data-testid="log-viewer"]')
log_text = log_viewer.inner_text()
has_dispatch = "Dispatching node" in log_text or "▶" in log_text
has_transition = "Edge transition" in log_text or "→" in log_text
```

### 4. Verify activity logs are visually distinct

Activity logs should be styled differently from regular task output:
- Italic text (`.log-activity` has `font-style: italic`)
- Dimmed color (lower opacity or secondary text color)
- Smaller font size (0.85em)

Take a screenshot showing activity logs:
```python
page.screenshot(path="/tmp/flowstate-e2e-activity-logs.png", full_page=True)
```

### 5. Check multiple nodes

Click on a different task node (e.g., `process`). Its log viewer should also show activity entries (at minimum a dispatch message).

### 6. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] Flow completes successfully
- [ ] Activity log entries appear in the log viewer (at least 1 dispatch + 1 edge transition)
- [ ] Activity entries are visually distinct from task output (italic, dimmed)
- [ ] Activity entries contain meaningful orchestrator messages (not raw JSON)
- [ ] No JavaScript console errors
- [ ] Completed within 10-minute timeout
