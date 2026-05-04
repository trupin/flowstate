# [SERVER-032] Serve UI from `importlib.resources` instead of `ui/dist/`

## Domain
server

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-008
- Blocks: —

## Spec References
- specs.md §13.4 Deployment & Installation — "UI packaging"

## Summary
`src/flowstate/server/app.py:23` currently mounts `StaticFiles(directory="ui/dist")` — a CWD-relative path that only works when the server is launched from the Flowstate dev repo root. Now that SHARED-008 bundles the built UI into the wheel at `src/flowstate/_ui_dist/`, switch the runtime to resolve the UI directory via `importlib.resources`, with a fallback to the repo-relative `ui/dist/` for dev.

## Acceptance Criteria
- [ ] The FastAPI app resolves the UI assets directory via `importlib.resources.files("flowstate") / "_ui_dist"` (cast to `Path` via `as_file`).
- [ ] Fallback: if the resolved directory is empty or missing `index.html` AND a sibling `ui/dist/` exists relative to the package source (dev mode), use that instead.
- [ ] The mount works regardless of CWD at server startup.
- [ ] If neither location has an `index.html`, log a warning (existing behavior preserved) but do not crash — the API still serves.
- [ ] SPA fallback (any GET path not matching `/api/*` or `/ws` serves `index.html`) continues to work with the new resolution.
- [ ] An integration test starts the app via `TestClient` and asserts `GET /` returns HTML with the expected `<title>` from the built UI (or skips cleanly when `_ui_dist/` is empty in the dev environment).

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/app.py` — the UI mount logic.
- `src/flowstate/server/ui_assets.py` (new, small helper) — resolves the UI directory.
- `tests/server/test_ui_serving.py` — new test.

### Key Implementation Details
```python
from importlib.resources import as_file, files
from pathlib import Path


def locate_ui_dir() -> Path | None:
    # 1. Packaged assets (installed wheel or editable dev install)
    try:
        packaged = files("flowstate") / "_ui_dist"
        with as_file(packaged) as path:
            if (path / "index.html").is_file():
                return path
    except (FileNotFoundError, ModuleNotFoundError):
        pass

    # 2. Dev fallback: ui/dist/ next to the source checkout
    pkg_root = Path(__file__).resolve().parent.parent  # .../src/flowstate
    repo_root = pkg_root.parent.parent                  # repo root
    dev_dist = repo_root / "ui" / "dist"
    if (dev_dist / "index.html").is_file():
        return dev_dist

    return None
```

In `app.py`:
```python
ui_dir = locate_ui_dir()
if ui_dir is not None:
    app.mount("/assets", StaticFiles(directory=ui_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa_fallback(full_path: str) -> HTMLResponse:
        if full_path.startswith("api/") or full_path.startswith("ws"):
            raise HTTPException(status_code=404)
        return HTMLResponse((ui_dir / "index.html").read_text())
else:
    logger.warning("No built UI found; serving API only.")
```

### Edge Cases
- Running pytest in editable install mode → `_ui_dist/` may be empty; fallback to `ui/dist/` catches this when the repo actually has a built UI.
- `as_file` on an already-extracted package returns the real filesystem path — no extra copy.
- Static assets like `favicon.ico` at the root of `_ui_dist/` → mount them the same way they're mounted today (whatever the current app does, preserve it).

## Testing Strategy
- **Unit test for `locate_ui_dir()`**: monkey-patch `files("flowstate")` to point at a `tmp_path` with an `index.html`; assert the path is returned.
- **Unit test for fallback**: same with an empty packaged dir but a seeded `ui/dist/` (harder to simulate in a test; a mock is fine).
- **Integration test**: `TestClient(app).get("/")` returns HTML containing something from the real built UI (skip with a clear reason if the dev environment has no built UI).

## E2E Verification Plan

### Verification Steps
1. Build the wheel via SHARED-008. Install it in a throwaway venv. From an arbitrary directory (that is a valid project), run `flowstate server` and open `http://127.0.0.1:9090` — the UI loads, assets come from the wheel.
2. In the Flowstate dev repo with a fresh `ui/dist/` (via `npm run build`) and no `_ui_dist/`, run `uv run flowstate server` — the UI still loads via the dev fallback.
3. Delete both `ui/dist/` and `_ui_dist/` in the dev repo; start the server — warning logged, API still responds, `GET /` returns 404 (or a placeholder).

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `locate_ui_dir()` implemented
- [ ] `app.py` mount uses the helper
- [ ] SPA fallback preserved
- [ ] Warning-but-don't-crash behavior preserved
- [ ] Unit + integration tests passing
- [ ] `/lint` passes
- [ ] E2E steps above verified
