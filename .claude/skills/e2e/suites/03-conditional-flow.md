# Suite 03: Conditional Flow (Judge Routing)

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_conditional.flow`
**Workspace**: `/tmp/flowstate-e2e-conditional`

## Purpose

Test a flow with conditional edges where the judge evaluates task output and decides the next transition. Validates the judge protocol works end-to-end with real Claude Code.

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
1. Click `e2e_conditional` in the sidebar
2. Click "Start Run" button
3. In the modal, click Submit/Start (no parameters needed)
4. The page should navigate to the Run Detail view

### 2. Monitor execution

Poll every 15 seconds. Track:
- Which nodes have been executed
- Whether a judge decision was made (check edge transitions in the run detail API response)
- Whether any cycle occurred (`implement` executed more than once, indicated by `generation > 1`)

Run staleness detection per `procedures/staleness-detection.md`.

### 3. Observe judge behavior

After `review` completes, the judge evaluates two conditions:
- "the review approves the code and finds no issues" → `ship` (exit)
- "the review found issues that need fixing" → `implement` (cycle)

**Both paths are valid.** The test validates that:
- A judge decision was actually made (a node after `review` enters `running` or `completed`)
- The chosen path was followed correctly

Check the UI for edge transitions — observe which node becomes active after `review` completes by watching `data-status` attributes on nodes.

### 4. Wait for terminal state

The flow can end in:
- `completed` — judge chose `ship` (the happy path)
- `completed` after a cycle — judge chose `implement`, then `review` → `ship`
- `budget_exceeded` or `paused` — if cycles continue until budget runs out

All are acceptable outcomes for this test.

### 5. Verify in UI

Take a screenshot of the final state:

```python
page.goto(f"http://localhost:9090/runs/{run_id}", wait_until="networkidle")
page.screenshot(path="/tmp/flowstate-e2e-conditional-final.png", full_page=True)
```

Check:
- The flow reached a terminal status (not still `running`)
- Node statuses are consistent (no node stuck in `running`)
- If cycles occurred, generation badges are visible on the cycled nodes

### 6. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] Flow starts and `implement` node completes
- [ ] `review` node completes
- [ ] Judge makes at least one routing decision (edge transition from `review` exists)
- [ ] Flow reaches a terminal state (`completed`, `paused`, or `budget_exceeded`)
- [ ] No stale tasks detected
- [ ] Completed within 10-minute timeout
