# Suite 09: Cycle Flow

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_cycle.flow`
**Workspace**: `/tmp/flowstate-e2e-cycle`

## Purpose

Test a flow with cycles (implement → verify → implement loop) and verify it eventually terminates either by the judge deciding all work is done, or by budget exhaustion. This validates cycle re-entry, generation tracking, and judge-based exit decisions.

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
1. Click `e2e_cycle` in the sidebar
2. Click "Start Run" button
3. In the modal, click Submit/Start (no parameters needed)
4. The page should navigate to the Run Detail view

### 2. Monitor execution and cycle count

Watch the UI every 15 seconds. Track:
- Which nodes show `data-status="running"` or `data-status="completed"`
- Whether generation badges appear on nodes (indicating cycles)
- Which node becomes active after `verify` completes

Log each cycle observed in the UI.

### 3. Staleness detection

Run per `procedures/staleness-detection.md`. The cycle flow is more likely to trigger staleness because it involves multiple judge evaluations.

### 4. Wait for terminal state

The flow can end with:
- `completed` — judge decided all items are done → flow exits via `complete` node
- `budget_exceeded` — the 10-minute budget ran out during cycles
- `paused` — an error occurred during a cycle

All are acceptable. The important thing is that the flow terminates.

### 5. Verify cycle occurred

Check the tasks in the final run detail:
```python
resp = httpx.get(f"http://localhost:9090/api/runs/{run_id}")
tasks = resp.json()["tasks"]
implement_tasks = [t for t in tasks if t["node_name"] == "implement"]
```

There should be at least 2 `implement` tasks (the initial run + at least one cycle back). If only 1 implement task exists, the judge went straight to `complete` after the first verify — this is still valid but less interesting.

### 6. Verify in UI

```python
page.goto(f"http://localhost:9090/runs/{run_id}", wait_until="networkidle")
page.screenshot(path="/tmp/flowstate-e2e-cycle-final.png", full_page=True)
```

Read the screenshot. Check:
- The flow reached a terminal status
- Node statuses are consistent
- If cycles occurred, the graph should show generation indicators

### 7. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] `plan` node completes
- [ ] At least one `implement` → `verify` cycle executes
- [ ] Judge makes at least one routing decision (edge from `verify` exists)
- [ ] Flow reaches a terminal state (not stuck in `running`)
- [ ] Ideally: at least one cycle back (implement generation > 1)
- [ ] No stale tasks detected
- [ ] Completed within 10-minute timeout
