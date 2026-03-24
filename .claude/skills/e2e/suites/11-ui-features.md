# Suite 11: Full-Stack Feature Verification

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_linear.flow`
**Workspace**: `/tmp/flowstate-e2e-linear` (reused)

## Purpose

Comprehensive verification of recently implemented features across all layers (engine, server, UI) using a single linear flow execution. Tests the full stack end-to-end rather than isolated components.

**Features tested:**
- **ENGINE-024**: Activity logs stored in DB and emitted via WebSocket
- **ENGINE-017/018**: Cancel/resume control flow (verified in Suite 05/06, cross-checked here)
- **SERVER-010**: API responses include `cwd`, `task_dir`, `worktree_path` fields
- **UI-020**: Thinking label transitions from "Thinking..." to "Thoughts"
- **UI-021**: Graph auto-updates on flow completion (no manual re-select)
- **UI-022**: Node details show cwd, task_dir paths
- **UI-023**: Flow detail panel shows settings, nodes, edges, params, DSL source

## Procedure

### Part A: Flow Detail Panel + API Structure (UI-023, SERVER-010)

Before starting any flow, verify the flow detail panel and API.

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

1. **Click `e2e_linear`** in the sidebar
2. **Verify detail panel** (UI-023):
   - `.flow-detail-panel` is visible (not a flow list)
   - `.flow-settings-grid` shows budget, context, on_error values
   - Source DSL section present (check for `page.get_by_text("Source DSL")`)
3. **Verify API response structure** (SERVER-010):
   ```python
   resp = httpx.get("http://127.0.0.1:9090/api/flows/e2e_linear")
   flow = resp.json()
   assert "ast_json" in flow  # Flow AST exposed
   assert "source_dsl" in flow  # DSL source exposed
   ```
4. **Screenshot**: `/tmp/flowstate-e2e-flow-detail.png`

### Part B: Start Flow

Start the flow via UI:
1. Click "Start Run" → modal opens
2. Click Submit in the modal
3. Navigate to run detail page

### Part C: Engine Activity Logs (ENGINE-024)

During and after execution, verify activity logs are stored and emitted:

1. **Check DB storage**: After flow completes, query task logs via API:
   ```python
   for task in data["tasks"]:
       logs_resp = httpx.get(f"http://127.0.0.1:9090/api/runs/{run_id}/tasks/{task['id']}/logs")
       logs_data = logs_resp.json()
       for log in logs_data.get("logs", []):
           if log["log_type"] == "system":
               content = json.loads(log["content"])
               if content.get("subtype") == "activity":
                   activity_messages.append(content["message"])
   ```
2. **Expected activity messages** (for a 3-node linear flow):
   - 3× "▶ Dispatching node '...' (generation 1)"
   - 2× "→ Edge transition: ... → ..."
3. **Verify messages contain node names**: "start", "process", "done" should appear

### Part D: Server API Fields (SERVER-010, UI-022)

After flow completes, verify the run detail API exposes all required fields:

```python
resp = httpx.get(f"http://127.0.0.1:9090/api/runs/{run_id}")
data = resp.json()

# SERVER-010: Task execution fields
for task in data["tasks"]:
    assert "cwd" in task, f"Task {task['node_name']} missing cwd"
    assert "task_dir" in task, f"Task {task['node_name']} missing task_dir"
    assert "node_type" in task  # Should be actual value, not hardcoded "task"
    assert "context_mode" in task  # Should be actual value, not hardcoded "handoff"
    assert task["cwd"] != "."  # Should be actual resolved path
```

### Part E: Graph Auto-Update (UI-021)

**Critical**: Do NOT reload the page after detecting completion via API. The graph should have updated automatically via WebSocket events.

1. Stay on the run detail page throughout execution
2. After API shows `completed`, wait 3 seconds for WebSocket propagation
3. Check current page (no reload): status badge should show "COMPLETED"
4. **Screenshot**: `/tmp/flowstate-e2e-graph-autoupdate.png`

### Part F: Thinking Label Transition (UI-020)

After completion, check for thinking blocks in the log viewer:

1. Click on a completed node to show its logs
2. Check for thinking blocks: `.log-thinking-header`
3. If present, verify completed ones show "Thoughts" (`.log-thinking-header-done`)

**Note**: Claude may not emit thinking blocks for simple tasks. If no thinking blocks are observed, this is NOT a failure — record as "no thinking blocks emitted by model".

### Part G: Node Directory Details (UI-022)

1. **Click on a completed node** (e.g., `start`)
2. **Verify directory section** (`.node-pill-dirs`) is visible
3. **Verify paths shown**: The expanded node should display cwd and task_dir
4. **Screenshot**: `/tmp/flowstate-e2e-node-details.png`

### Part H: Clean up

```python
context.close()
browser.close()
```

## Success Criteria

**Engine (ENGINE-024):**
- [ ] Activity logs stored in DB (at least 3 dispatch + 2 transition = 5 messages)
- [ ] Activity messages reference correct node names

**Server (SERVER-010):**
- [ ] API returns `cwd`, `task_dir` for each task (not hardcoded defaults)
- [ ] API returns `node_type` and `context_mode` from actual DB values
- [ ] Flow detail API includes `ast_json` and `source_dsl`

**UI-023 (Flow Detail Panel):**
- [ ] Flow detail panel visible when flow selected (not a flow list)
- [ ] Settings displayed (budget, context, on_error visible)

**UI-021 (Graph Auto-Update):**
- [ ] Graph shows completed status WITHOUT page reload

**UI-022 (Node Directories):**
- [ ] Node directory section visible when clicking completed node

**UI-020 (Thinking Label):**
- [ ] If thinking blocks present, completed ones show "Thoughts" (not "Thinking...")
- [ ] If no thinking blocks, record as expected (model-dependent)

**General:**
- [ ] Flow completed successfully
- [ ] Completed within 10-minute timeout
