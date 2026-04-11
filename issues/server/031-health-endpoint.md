# [SERVER-031] `GET /health` endpoint returning project slug + version

## Domain
server

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SERVER-026
- Blocks: —

## Spec References
- specs.md §13.4 Deployment & Installation — "Health check"

## Summary
Add a `GET /health` endpoint that a local orchestrator (or the E2E verification script for this whole batch) can hit to confirm the server is live, which project it's serving, and which version of Flowstate is installed. Small issue, but it's the anchor point every later deployability test depends on.

## Acceptance Criteria
- [ ] `GET /health` returns HTTP 200 with JSON body:
  ```json
  {
    "status": "ok",
    "version": "0.1.0",
    "project": {
      "slug": "my-app-a1b2c3d4",
      "root": "/absolute/path/to/project"
    }
  }
  ```
- [ ] The endpoint does not require authentication (matches the "no auth" posture).
- [ ] `version` is read from the package metadata (`importlib.metadata.version("flowstate")`), not hardcoded.
- [ ] Endpoint is available before any heavy startup work finishes (so orchestrators can poll for readiness).
- [ ] An integration test using FastAPI `TestClient` asserts the response shape.

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/routes.py` (or a new `src/flowstate/server/health.py`) — add the handler.
- `src/flowstate/server/app.py` — register the router.
- `tests/server/test_health.py` — new test.

### Key Implementation Details
```python
from importlib.metadata import version as pkg_version
from fastapi import APIRouter

router = APIRouter()

@router.get("/health")
def health(project: Project = Depends(get_project)) -> dict:
    return {
        "status": "ok",
        "version": pkg_version("flowstate"),
        "project": {
            "slug": project.slug,
            "root": str(project.root),
        },
    }
```

`get_project` is a FastAPI dependency that returns the `Project` stored on `app.state` by the app factory. If this pattern isn't already in use, set it up minimally — SERVER-026 may already have done so.

### Edge Cases
- `importlib.metadata.version("flowstate")` raises `PackageNotFoundError` when running from a source checkout that isn't installed → fall back to a constant `"0.0.0+dev"`.
- Project not mounted on `app.state` (tests using bare app without `Project`) → accept a fallback or require tests to build a `Project`.

## Testing Strategy
- `TestClient(app).get("/health")` → 200, JSON contains `status`, `version`, `project.slug`, `project.root`.
- `project.slug` matches the slug derived from the fixture's `tmp_path`.

## E2E Verification Plan

### Verification Steps
1. `flowstate server` in `/tmp/fs-health/`.
2. `curl http://127.0.0.1:9090/health` → 200 JSON.
3. Parse the JSON and assert `project.root` ends in `fs-health` and `slug` starts with `fs-health-`.
4. Switch to a different project and repeat; assert the slug changes.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `/health` endpoint implemented
- [ ] Version read from package metadata with dev fallback
- [ ] Integration test passing
- [ ] `/lint` passes
- [ ] E2E steps above verified
