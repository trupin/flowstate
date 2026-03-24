# Suite 01: Smoke Test

**Timeout**: 60 seconds total
**Flow files needed**: `e2e_linear.flow` (must exist in `./flows/`, not executed)
**Workspace**: none (no flow execution)

## Purpose

Verify the server starts, UI loads correctly, flows are discovered, and basic navigation works. This is the foundation — if smoke fails, skip all other suites.

## Procedure

### 1. Verify server health

```bash
curl -s http://127.0.0.1:9090/api/flows
```

Expect: HTTP 200 with a JSON array. The array should contain at least one flow (the `e2e_linear` flow copied during server restart).

### 2. Launch Playwright and load the UI

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()

    # Capture console errors
    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

    page.goto("http://localhost:9090", wait_until="networkidle")
```

### 3. Take initial screenshot

```python
page.screenshot(path="/tmp/flowstate-e2e-smoke-loaded.png", full_page=True)
```

Read the screenshot to verify the page rendered correctly.

### 4. Verify sidebar

- The sidebar element exists: `page.locator('.sidebar')` should be visible
- At least one flow appears: `page.locator('[data-testid^="sidebar-flow-"]')` should have count >= 1

### 5. Verify no error banner

`page.locator('[data-testid="error-banner"]')` should NOT be visible (count == 0 or hidden).

### 6. Click a flow in the sidebar

Click the first flow item. After clicking:
- The graph view should become visible with at least one node
- A "Start Run" or similar action button should be visible

### 7. Check console errors

After all interactions, check the `console_errors` list. Report any errors found.

### 8. Clean up

```python
context.close()
browser.close()
```

## Success Criteria

- [ ] Server returns 200 on `GET /api/flows`
- [ ] UI page loads without blank screen
- [ ] Sidebar shows at least one flow
- [ ] No error banner visible
- [ ] Clicking a flow shows its graph
- [ ] Zero JavaScript console errors
