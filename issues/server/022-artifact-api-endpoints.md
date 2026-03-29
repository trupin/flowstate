# [SERVER-022] Artifact upload/download REST endpoints

## Domain
server

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: STATE-011
- Blocks: ENGINE-067, ENGINE-068, UI-066

## Spec References
- specs.md Section 9.6 — "API-Based Artifact Protocol"
- specs.md Section 10.1 — "REST API"

## Summary
Add REST API endpoints for agents to upload and download task artifacts (DECISION.json, SUMMARY.md, OUTPUT.json). These endpoints replace the file-based handoff mechanism. All agents — whether running on the host or in a sandbox — POST their coordination artifacts to these endpoints, and the engine reads them via the repository layer. The UI can also read artifacts to display decision reasoning and task summaries.

## Acceptance Criteria
- [ ] `POST /api/runs/{run_id}/tasks/{task_id}/artifacts/{name}` stores artifact content in DB
- [ ] `GET /api/runs/{run_id}/tasks/{task_id}/artifacts/{name}` returns artifact content
- [ ] `GET /api/runs/{run_id}/tasks/{task_id}/artifacts` lists all artifacts for a task
- [ ] POST accepts both `application/json` and `text/markdown` content types
- [ ] POST returns 201 on create, 200 on update (upsert)
- [ ] GET returns 404 if artifact not found
- [ ] GET single artifact returns the content with the stored content_type header
- [ ] Artifacts are included in the run detail response (`GET /api/runs/{run_id}`) under each task
- [ ] Invalid task_id returns 404

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/routes.py` — add artifact endpoints + include artifacts in run detail
- `tests/server/test_routes.py` — add artifact endpoint tests

### Key Implementation Details

**Upload endpoint:**
```python
@router.post("/api/runs/{run_id}/tasks/{task_id}/artifacts/{name}")
async def upload_artifact(
    run_id: str, task_id: str, name: str, request: Request
) -> JSONResponse:
    body = await request.body()
    content_type = request.headers.get("content-type", "application/json")
    content = body.decode("utf-8")
    db.save_artifact(task_id, name, content, content_type)
    return JSONResponse({"status": "ok", "name": name}, status_code=201)
```

**Download endpoint:**
```python
@router.get("/api/runs/{run_id}/tasks/{task_id}/artifacts/{name}")
async def download_artifact(
    run_id: str, task_id: str, name: str, request: Request
) -> Response:
    artifact = db.get_artifact(task_id, name)
    if not artifact:
        raise FlowstateError("Artifact not found", status_code=404)
    return Response(content=artifact.content, media_type=artifact.content_type)
```

**List endpoint:**
```python
@router.get("/api/runs/{run_id}/tasks/{task_id}/artifacts")
async def list_artifacts(
    run_id: str, task_id: str, request: Request
) -> JSONResponse:
    artifacts = db.list_artifacts(task_id)
    return JSONResponse([
        {"name": a.name, "content_type": a.content_type, "created_at": a.created_at}
        for a in artifacts
    ])
```

**Run detail enrichment:** In the `GET /api/runs/{run_id}` handler, include artifacts for each task:
```python
task_data["artifacts"] = [
    {"name": a.name, "content_type": a.content_type}
    for a in db.list_artifacts(task.id)
]
```

**Validation:**
- Verify `task_id` belongs to `run_id` (join check)
- Artifact name validation: alphanumeric + hyphens + underscores, max 64 chars
- Content size limit: 1MB (reject with 413)

### Edge Cases
- Agent POSTs artifact before task is marked running (race): allow it, the task_execution record exists from creation
- Agent POSTs same artifact twice: upsert semantics, last write wins
- Content-Type not provided: default to `application/json`
- Binary content: not supported in v1 (TEXT column), return 415 for non-text types

## Testing Strategy
- Unit tests with TestClient:
  - POST artifact, GET it back, verify content matches
  - POST upsert, verify content updated
  - GET non-existent artifact → 404
  - List artifacts for a task
  - Artifacts appear in run detail response
  - Invalid task_id → 404

## E2E Verification Plan

### Verification Steps
1. Start server: `uv run flowstate server`
2. Create and start a flow run
3. `curl -X POST localhost:9090/api/runs/{id}/tasks/{tid}/artifacts/decision -H 'Content-Type: application/json' -d '{"decision":"ship","reasoning":"done","confidence":0.9}'`
4. `curl localhost:9090/api/runs/{id}/tasks/{tid}/artifacts/decision` → verify content
5. `curl localhost:9090/api/runs/{id}/tasks/{tid}/artifacts` → verify list
6. `curl localhost:9090/api/runs/{id}` → verify artifacts in task detail

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
