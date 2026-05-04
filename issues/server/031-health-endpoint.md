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
- [x] `GET /health` returns HTTP 200 with JSON body:
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
- [x] The endpoint does not require authentication (matches the "no auth" posture).
- [x] `version` is read from the package metadata (`importlib.metadata.version("flowstate")`), not hardcoded.
- [x] Endpoint is available before any heavy startup work finishes (so orchestrators can poll for readiness).
- [x] An integration test using FastAPI `TestClient` asserts the response shape.

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

### Post-Implementation Verification (2026-04-11)

Canonical TEST-17 journey executed against the real CLI. Full transcript
shared across SERVER-028/029/030/031:

```
===== STEP 4: SERVER-031 /health endpoint =====
# cmd: nohup uv --project <worktree> run flowstate server --port 9097 \
#         > /tmp/fs-phase312-server.log 2>&1 &
# poll: curl -sf http://127.0.0.1:9097/health > /tmp/fs-phase312-health.json
server pid=95999
ready after 2s
{"status":"ok","version":"0.1.0","project":{"slug":"fs-phase312-proj-687706de","root":"/private/tmp/fs-phase312-proj"}}
STEP 4 PASS
```

Python assertions run against the parsed JSON:

```python
j = json.load(open('/tmp/fs-phase312-health.json'))
assert j['status'] == 'ok'
assert isinstance(j['version'], str) and j['version']
assert j['project']['slug'].startswith('fs-phase312-proj-')
assert j['project']['root'] == '/private/tmp/fs-phase312-proj'
```

All four assertions passed. Notes:

- `version` came back as `"0.1.0"` (the real PEP 440 value from
  `importlib.metadata.version("flowstate")` because the worktree has
  been `pip install -e`'d into the active venv). The
  `PackageNotFoundError` fallback path to `"0.0.0+dev"` is exercised
  by `tests/server/test_health.py::test_health_version_falls_back_on_package_not_found`
  (monkeypatched `pkg_version` raises, handler still returns 200).
- `project.root` is `/private/tmp/fs-phase312-proj`, not
  `/tmp/fs-phase312-proj`. On macOS `/tmp` is a symlink to
  `/private/tmp` and `Project.root` is always `.resolve()`-d (see
  `src/flowstate/config.py::build_project`). This is the canonical
  absolute path the sprint contract (TEST-17) requires.
- `project.slug` is `fs-phase312-proj-687706de`: the 8-character hash
  suffix is derived from `sha1("/private/tmp/fs-phase312-proj")` and
  is stable across restarts of the same directory.
- The `/health` endpoint responded without authentication via plain
  `curl -sf` — no header handshake required, matching the spec's
  "no auth" posture for v0.1.
- The route is registered on the bare FastAPI app via
  `flowstate.server.health.router` (no `/api` prefix), and
  `app.py::spa_fallback` explicitly excludes `full_path == "health"`
  as a belt-and-braces guard against the catch-all shadowing it.
- Integration tests in `tests/server/test_health.py` cover:
    - `GET /health` returns 200 with the correct shape (`status`,
      `version`, `project.slug`, `project.root`).
    - The payload exposes exactly three top-level keys and
      `project` contains exactly two keys — no `db_path`,
      `workspaces`, or `data_dir` leaks (TEST-16).
    - The `PackageNotFoundError` → `"0.0.0+dev"` fallback path.
    - The endpoint survives 5 sequential requests (polling loop).

## E2E Verification Log — Fix-loop round 1 (2026-04-11)

The Phase 31.2 evaluator flagged TEST-10 because the CLI had no
top-level `--version` flag. The `/health` endpoint already resolved
the version from `importlib.metadata.version("flowstate")` with a
`"0.0.0+dev"` fallback; this round wires the **same** resolver into
the CLI via an eager Typer callback (`src/flowstate/cli.py::_version_callback`).
The two call sites now share both the sentinel string and the fallback
semantics, so `flowstate --version` and `GET /health` agree on what
"dev build" looks like.

Verified against the real CLI (outside any project, so the walk-up
must not reach a `flowstate.toml`):

```
$ cd /tmp/fs-fixloop-test   # no ancestor flowstate.toml
$ uv --project <worktree> run flowstate --version
flowstate 0.1.0
exit=0

$ uv --project <worktree> run flowstate -V
flowstate 0.1.0
exit=0
```

And the corresponding `/health` value is still identical:

```
$ curl -s http://127.0.0.1:9193/health
{"status":"ok","version":"0.1.0","project":{"slug":"fs-fixloop-server-b8c6ea27","root":"/private/tmp/fs-fixloop-server"}}
```

New unit tests in `tests/server/test_cli_errors.py`:
- `TestCommandsThatBypassProjectCheck::test_version_long_flag_works_outside_project`
- `TestCommandsThatBypassProjectCheck::test_version_short_flag_works_outside_project`

Both assert exit 0, a non-empty version-looking string, and no
"No flowstate.toml found" in stdout/stderr. The helper doesn't
hard-code `"0.1.0"` because the same code must keep working on source
checkouts where the fallback `"0.0.0+dev"` applies.

## Completion Checklist
- [x] `/health` endpoint implemented
- [x] Version read from package metadata with dev fallback
- [x] Integration test passing
- [x] `/lint` passes
- [x] E2E steps above verified
- [x] Fix-loop round 1: top-level `--version` / `-V` flag added,
      sharing the `/health` resolver + `0.0.0+dev` fallback.
