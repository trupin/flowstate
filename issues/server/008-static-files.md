# [SERVER-008] Static File Serving (React build)

## Domain
server

## Status
todo

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: SERVER-001
- Blocks: none

## Spec References
- specs.md Section 10.1 — "Pages" (the UI pages served by the static files)
- agents/04-server.md — "Static File Serving"

## Summary
Mount the React build output (`ui/dist/`) as static files in the FastAPI app, enabling the Flowstate web UI to be served from the same process as the API. The root `/` route serves `index.html`, and all non-API, non-WebSocket routes fall through to the SPA (single-page application) so that React Router can handle client-side navigation. This is a P2 issue because during development the React dev server (Vite) proxies API calls to the FastAPI backend, so static file serving is only needed for production deployments.

## Acceptance Criteria
- [ ] When `ui/dist/` exists, static files are served from that directory
- [ ] `GET /` returns `ui/dist/index.html` with `Content-Type: text/html`
- [ ] `GET /assets/main.js` (or similar) returns the correct JS file from `ui/dist/assets/`
- [ ] `GET /any/unknown/path` (not starting with `/api/` or `/ws`) returns `index.html` (SPA fallback)
- [ ] `/api/*` routes are NOT intercepted by static file serving — they pass through to the API router
- [ ] `/ws` WebSocket endpoint is NOT intercepted by static file serving
- [ ] When `ui/dist/` does NOT exist, the app still starts without error (static files are optional)
- [ ] A warning is logged if `ui/dist/` is not found
- [ ] All tests pass: `uv run pytest tests/server/test_static_files.py`

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/app.py` — add static file mounting to `create_app`
- `tests/server/test_static_files.py` — all tests

### Key Implementation Details

#### Static File Mounting Strategy

FastAPI's `StaticFiles` mount with `html=True` serves `index.html` for directory requests, but it does NOT provide SPA fallback for arbitrary paths. We need a custom approach:

```python
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

UI_DIST_DIR = Path(__file__).parent.parent.parent.parent / "ui" / "dist"


def mount_static_files(app: FastAPI, dist_dir: Path | None = None) -> None:
    """Mount the React build output as static files with SPA fallback."""
    dist = dist_dir or UI_DIST_DIR

    if not dist.exists():
        logger.warning(
            "UI dist directory not found at %s. "
            "Static file serving is disabled. "
            "Run 'cd ui && npm run build' to build the UI.",
            dist,
        )
        return

    index_html = dist / "index.html"
    if not index_html.exists():
        logger.warning("index.html not found in %s. Static file serving is disabled.", dist)
        return

    # Mount static assets (JS, CSS, images, etc.)
    # This must be mounted BEFORE the SPA fallback route
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

    # Mount any other static files at root level (favicon.ico, manifest.json, etc.)
    # Use a catch-all route for SPA fallback instead of StaticFiles(html=True)
    # because html=True does not handle arbitrary nested paths

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        favicon_path = dist / "favicon.ico"
        if favicon_path.exists():
            return FileResponse(str(favicon_path))
        return FileResponse(str(index_html))  # fallback

    # SPA fallback: any GET request not matching /api/* or /ws returns index.html
    # This must be registered AFTER all API routes
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> HTMLResponse:
        # Check if it's a real static file first
        static_file = dist / full_path
        if static_file.exists() and static_file.is_file():
            return FileResponse(str(static_file))
        # Otherwise serve index.html for client-side routing
        return HTMLResponse(content=index_html.read_text())
```

#### Integration in `create_app`

```python
def create_app(config: FlowstateConfig | None = None) -> FastAPI:
    # ... existing setup ...

    # Include API router FIRST (has /api prefix)
    app.include_router(router)

    # Mount static files LAST (catch-all SPA fallback)
    mount_static_files(app)

    return app
```

The order is critical:
1. CORS middleware
2. Exception handlers
3. API routes (`/api/*`)
4. WebSocket endpoint (`/ws`)
5. Static files + SPA fallback (catch-all `/{path:path}`)

FastAPI matches routes in registration order, so API routes registered first will take priority over the SPA catch-all.

#### Dist Directory Resolution

The default `UI_DIST_DIR` is resolved relative to the `app.py` source file, assuming the standard project layout:

```
src/flowstate/server/app.py  →  ../../../../ui/dist/
```

This works for development. For production deployments, the dist directory can be configured or overridden. A future enhancement could add a `ui_dist_dir` config option to `FlowstateConfig`.

### Edge Cases
- `ui/dist/` does not exist (UI not built): log warning, skip mounting, API still works.
- `ui/dist/index.html` does not exist (partial build): log warning, skip mounting.
- Request for `/api/flows` with static files mounted: API route takes priority because it's registered first.
- Request for `/ws` WebSocket: WebSocket route takes priority because it's registered before the catch-all.
- Request for `/assets/main.abc123.js`: served from `StaticFiles` mount.
- Request for `/some/react/route`: SPA fallback returns `index.html`, React Router handles it.
- Request for a file that exists in `dist/` but is not in `/assets/`: the SPA fallback checks if the file exists before returning `index.html`.
- Concurrent access to static files: `StaticFiles` and `FileResponse` are safe for concurrent reads.

## Testing Strategy

Create `tests/server/test_static_files.py`:

1. **test_static_files_served** — Create a tmp dir mimicking `ui/dist/` with `index.html` and `assets/main.js`. Mount via `mount_static_files(app, dist_dir=tmp)`. Use `TestClient`. Verify `GET /` returns `index.html` content. Verify `GET /assets/main.js` returns the JS content.

2. **test_spa_fallback** — Same setup. Verify `GET /some/unknown/path` returns `index.html` content (not 404).

3. **test_api_routes_not_intercepted** — Mount static files AND include the API router. Verify `GET /api/flows` hits the API router (not the static file fallback). This requires a mock `FlowRegistry` to be set up.

4. **test_no_dist_dir** — Call `mount_static_files(app, dist_dir=Path("/nonexistent"))`. Verify no routes are added (no crash). Verify the app still responds to API routes.

5. **test_missing_index_html** — Create a tmp dir with `assets/` but no `index.html`. Verify `mount_static_files` logs a warning and does not mount.

6. **test_real_static_file_priority** — Create `dist/robots.txt`. Verify `GET /robots.txt` returns the file content, not `index.html`.

7. **test_favicon** — Create `dist/favicon.ico`. Verify `GET /favicon.ico` returns the icon file.

Use `tmp_path` fixture to create temporary dist directories with known file contents. Use `TestClient` for all HTTP assertions.
