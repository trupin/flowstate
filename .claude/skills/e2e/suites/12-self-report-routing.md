# Suite 12: Self-Report Routing (ENGINE-023)

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_self_report.flow`
**Workspace**: `/tmp/flowstate-e2e-self-report`

## Purpose

Verify that self-report routing works end-to-end when `judge = false`. Instead of spawning a separate judge subprocess to evaluate conditional edges, the task agent itself writes a DECISION.json file with its routing decision. The executor reads this file and follows the chosen edge.

This tests ENGINE-023 and DSL-007 (the `judge` parameter).

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
1. Click `e2e_self_report` in the sidebar
2. Click "Start Run" button
3. In the modal, click Submit/Start
4. Navigate to run detail

### 2. Monitor execution

Poll `GET /api/runs/{run_id}` until completion or timeout.

Key observations:
- The `decide` node has conditional edges but `judge = false` at the flow level
- The executor should append routing instructions to the `decide` task's prompt
- The task agent should write a DECISION.json to its task directory
- The executor should read DECISION.json and route accordingly

### 3. Verify self-report routing

After the flow completes, verify via API:

```python
resp = httpx.get(f"http://127.0.0.1:9090/api/runs/{run_id}")
data = resp.json()
```

Check:
- The flow completed (status == "completed")
- An edge transition occurred from `decide` to either `done` (quality acceptable) or back to `analyze` (retry)
- The edge transition has `edge_type: "conditional"` in the edges list
- No JUDGE_STARTED or JUDGE_DECIDED events in the activity logs (since judge is disabled)

Verify DECISION.json was written by checking the task's activity logs:

```python
# Look for activity logs — should have edge transition but NO judge decision
activity_logs = [...]  # extract from task logs as in Suite 10
has_judge_log = any("Judge decided" in m for m in activity_logs)
has_edge_log = any("Edge transition" in m for m in activity_logs)
```

- `has_judge_log` should be **False** (no judge was invoked)
- `has_edge_log` should be **True** (edge was traversed)

### 4. Verify DECISION.json exists

Check the task directory for the DECISION.json file:

```bash
# Find the task dir for the 'decide' node
find ~/.flowstate/runs/{run_id}/tasks/decide-* -name DECISION.json 2>/dev/null
```

If found, read and verify it contains valid routing JSON:
```json
{
  "decision": "done",
  "confidence": 0.9,
  "reasoning": "..."
}
```

### 5. Take final screenshot

```python
page.screenshot(path="/tmp/flowstate-e2e-self-report-final.png", full_page=True)
```

### 6. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] Flow starts and completes with `judge = false`
- [ ] Self-report routing works (task writes DECISION.json, executor reads it)
- [ ] Edge transition occurs without judge subprocess
- [ ] No JUDGE_STARTED/JUDGE_DECIDED activity logs (judge was not invoked)
- [ ] DECISION.json exists in the task directory
- [ ] No stale tasks detected
- [ ] Completed within 10-minute timeout

**Note**: The `decide` task may route to either `done` or back to `analyze` — both are valid outcomes. The test verifies the routing mechanism, not the specific decision.
