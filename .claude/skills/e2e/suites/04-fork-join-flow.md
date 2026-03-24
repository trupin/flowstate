# Suite 04: Fork-Join Flow

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_fork_join.flow`
**Workspace**: `/tmp/flowstate-e2e-forkjoin`

## Purpose

Verify that parallel fork-join execution works correctly: after the entry node completes, two tasks run in parallel, and the exit node starts only after both complete.

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
1. Click `e2e_fork_join` in the sidebar
2. Click "Start Run" button
3. In the modal, click Submit/Start (no parameters needed)
4. The page should navigate to the Run Detail view

### 2. Monitor the fork

After `analyze` completes, observe the UI for both `create_data` and `create_schema` nodes becoming active. Periodically reload or watch for status changes via `data-status` attributes on nodes. Take a screenshot when both fork targets are visible:

```python
page.screenshot(path="/tmp/flowstate-e2e-forkjoin-parallel.png", full_page=True)
```

**Key check**: Both fork tasks should start without waiting for each other. Verify by checking that both have `started_at` timestamps before either has completed.

### 3. Monitor the join

After both `create_data` and `create_schema` complete, `validate` should start. Verify:
- `validate` does NOT start while either fork task is still running
- `validate` starts within 30 seconds of the last fork task completing

Run staleness detection per `procedures/staleness-detection.md`.

### 4. Wait for completion

Poll until `completed` or timeout.

### 5. Verify in UI

```python
page.goto(f"http://localhost:9090/runs/{run_id}", wait_until="networkidle")
```

Check:
- `[data-testid="node-analyze"][data-status="completed"]` visible
- `[data-testid="node-create_data"][data-status="completed"]` visible
- `[data-testid="node-create_schema"][data-status="completed"]` visible
- `[data-testid="node-validate"][data-status="completed"]` visible
- `[data-testid="flow-status"]` shows "completed"

Take final screenshot:
```python
page.screenshot(path="/tmp/flowstate-e2e-forkjoin-final.png", full_page=True)
```

### 6. Verify output files

```bash
test -f /tmp/flowstate-e2e-forkjoin/data.json && echo "data.json exists" || echo "MISSING"
test -f /tmp/flowstate-e2e-forkjoin/schema.json && echo "schema.json exists" || echo "MISSING"
```

### 7. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] `analyze` completes
- [ ] Both `create_data` and `create_schema` start (fork works)
- [ ] Both fork targets complete
- [ ] `validate` starts only after both forks complete (join works)
- [ ] Flow completes successfully
- [ ] Output files (`data.json`, `schema.json`) exist
- [ ] No stale tasks detected
- [ ] Completed within 10-minute timeout
