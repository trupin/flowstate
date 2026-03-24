# Suite 07: Error Handling (on_error = pause)

**Timeout**: 5 minutes total
**Flow files needed**: `e2e_error.flow`
**Workspace**: `/tmp/flowstate-e2e-error`

## Purpose

Test the error handling mechanism: when a task fails, the flow should pause (per `on_error = pause`) rather than continuing or crashing. Verify the UI shows the failure state clearly.

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
1. Click `e2e_error` in the sidebar
2. Click "Start Run" button
3. In the modal, click Submit/Start (no parameters needed)
4. The page should navigate to the Run Detail view

### 2. Wait for `start` to complete

Watch the UI until the `start` node shows `data-status="completed"`. This proves the flow is running.

### 3. Wait for `risky` to finish

Watch the UI until the `risky` node shows a terminal `data-status` (`completed` or `failed`).

**Important caveat**: Claude Code agents are resourceful and may successfully complete the "risky" task despite it being designed to fail. Both outcomes are informative:
- If `risky` **fails**: The `on_error = pause` mechanism should kick in — the run should enter `paused` status.
- If `risky` **succeeds**: The flow continues to `done` and completes normally. This is not a test failure — it demonstrates Claude's resilience. Note it in the summary.

### 4. If task failed: verify pause behavior

If `risky` failed:
- Watch the UI until `[data-testid="flow-status"]` shows "paused"
- Take screenshot:
  ```python
  page.screenshot(path="/tmp/flowstate-e2e-error-paused.png", full_page=True)
  ```
- Verify in UI:
  - `[data-testid="node-risky"]` has `data-status="failed"`
  - `[data-testid="flow-status"]` shows "paused"
  - Error message is visible somewhere in the UI

After verification, cancel the paused run via UI — click the "Cancel" button in the controls panel.

Run `procedures/process-cleanup.md`.

### 5. If task succeeded: verify normal completion

If `risky` succeeded:
- Wait for the flow to complete normally
- Note in the summary: "risky task succeeded despite being designed to fail — Claude worked around the error"
- This is still a PASS for the suite

### 6. Take final screenshot

```python
page.screenshot(path="/tmp/flowstate-e2e-error-final.png", full_page=True)
```

### 7. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

**If task fails (expected path):**
- [ ] `start` completes successfully
- [ ] `risky` reaches `failed` status
- [ ] Run enters `paused` status (on_error = pause worked)
- [ ] UI shows failure clearly (node status, error message)

**If task succeeds (Claude worked around the error):**
- [ ] Flow completes normally
- [ ] Note the observation in the summary

**Either way:**
- [ ] No stale tasks detected
- [ ] No orphan processes after cleanup
- [ ] Completed within 5-minute timeout
