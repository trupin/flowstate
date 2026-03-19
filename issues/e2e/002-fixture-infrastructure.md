# [E2E-002] E2E Fixture Infrastructure

## Domain
e2e

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: E2E-001, SERVER-001
- Blocks: E2E-003, E2E-004, E2E-005, E2E-006, E2E-007, E2E-008, E2E-009, E2E-010, E2E-011, E2E-012, E2E-013, E2E-014

## Spec References
- specs.md Section 10 — "Web Interface"
- specs.md Section 13 — "Configuration"

## Summary
Create the pytest fixture hierarchy and flow fixture templates that all E2E tests depend on. The fixtures start a real FastAPI server (with mock subprocess manager) in a background thread, provide isolated state (unique port, temp database, temp watch directory) per test module, and reset state between tests. Flow fixture templates are Python string constants representing common .flow file patterns.

## Acceptance Criteria
- [ ] `tests/e2e/conftest.py` exists with the full fixture hierarchy
- [ ] `tests/e2e/flow_fixtures.py` exists with 8 flow templates + `write_flow()` helper
- [ ] `playwright>=1.58.0` added to `[dependency-groups] e2e` in pyproject.toml
- [ ] Session-scoped `browser` fixture launches headless Chromium
- [ ] Module-scoped `server_and_mock` fixture starts uvicorn in a background thread with unique port, isolated DB, isolated watch_dir
- [ ] Test-scoped `page` fixture creates a fresh Playwright page
- [ ] Autouse `reset_state` fixture resets mock config, clears watch_dir, and truncates DB between tests
- [ ] `wait_for_flow_discovery(base_url, flow_name, timeout)` helper polls GET /api/flows instead of sleeping
- [ ] `uv run pytest tests/e2e/ --co` collects without errors (no actual tests yet, just fixture validation)

## Technical Design

### Files to Create/Modify
- `tests/e2e/conftest.py` — fixture hierarchy
- `tests/e2e/flow_fixtures.py` — flow templates + helpers
- `pyproject.toml` — add e2e dependency group

### Fixture Hierarchy

```python
# Session: one Chromium browser
@pytest.fixture(scope="session")
def browser():
    pw = sync_playwright().start()
    b = pw.chromium.launch(headless=True)
    yield b
    b.close()
    pw.stop()

# Module: server + mock + isolated state
@pytest.fixture(scope="module")
def server_and_mock(tmp_path_factory):
    mock = MockSubprocessManager()
    port = _find_free_port()
    data_dir = tmp_path_factory.mktemp("data")
    watch_dir = tmp_path_factory.mktemp("flows")
    config = FlowstateConfig(
        server_port=port,
        database_path=str(data_dir / "flowstate.db"),
        watch_dir=str(watch_dir),
    )
    app = create_app(config=config, subprocess_manager=mock)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_for_server(port)
    yield mock, f"http://localhost:{port}", watch_dir
    server.should_exit = True
    thread.join(timeout=10)

# Convenience fixtures that unpack server_and_mock
@pytest.fixture(scope="module")
def mock_subprocess(server_and_mock): return server_and_mock[0]

@pytest.fixture(scope="module")
def base_url(server_and_mock): return server_and_mock[1]

@pytest.fixture(scope="module")
def watch_dir(server_and_mock): return server_and_mock[2]

# Test: fresh page
@pytest.fixture
def page(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    p = ctx.new_page()
    p.goto(base_url)
    yield p
    p.close()
    ctx.close()

# Autouse: reset state between tests
@pytest.fixture(autouse=True)
def reset_state(mock_subprocess, watch_dir, base_url):
    mock_subprocess.reset()
    for f in watch_dir.glob("*.flow"):
        f.unlink()
    httpx.post(f"{base_url}/api/_test/reset", timeout=5)
    yield
```

### Flow Fixtures

8 templates in `flow_fixtures.py` as Python string constants with `{workspace}` placeholder:
- `LINEAR_FLOW` — entry→task→exit (3 nodes, 2 unconditional edges)
- `FORK_JOIN_FLOW` — entry→[A,B]→exit
- `CONDITIONAL_FLOW` — entry→review→{ship when "approved", entry when "needs work"}
- `CYCLE_FLOW` — entry→impl→verify→{done when "all done", impl when "more work"}
- `PARAMETERIZED_FLOW` — with `param focus: string` and `param verbose: bool`
- `FAILING_TASK_FLOW` — entry→risky→exit (risky will be configured to fail)
- `INVALID_FLOW` — garbage text (parse error)
- `FLOW_WITH_TYPE_ERROR` — valid syntax but missing exit node (S2 violation)

```python
def write_flow(watch_dir: Path, filename: str, template: str, workspace: Path) -> Path:
    content = template.format(workspace=str(workspace))
    path = watch_dir / filename
    path.write_text(content)
    return path

def wait_for_flow_discovery(base_url: str, flow_name: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = httpx.get(f"{base_url}/api/flows", timeout=1)
        if any(f["name"] == flow_name for f in resp.json()):
            return
        time.sleep(0.3)
    raise TimeoutError(f"Flow '{flow_name}' not discovered within {timeout}s")
```

### Edge Cases
- Server fails to start: `pytest.skip()` instead of cascading failures
- Port conflict: `_find_free_port()` uses socket bind to port 0
- Watch dir cleanup between tests: glob + unlink all .flow files

## Testing Strategy
Verify fixtures work by running `uv run pytest tests/e2e/ --co` (collection only). Write a minimal smoke test in `tests/e2e/test_smoke.py` that just verifies the server is reachable (`GET /api/flows` returns 200).
