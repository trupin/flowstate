# [STATE-011] Task artifact storage table and repository CRUD

## Domain
state

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: SERVER-022, ENGINE-068

## Spec References
- specs.md Section 9.6 — "API-Based Artifact Protocol"
- specs.md Section 6.5 — "Conditional Edge Execution"

## Summary
Add a `task_artifacts` table to store all task coordination data in the database instead of on the filesystem. This is the foundation for the universal artifact protocol: agents submit artifacts via the REST API, and the engine both writes and reads artifacts through the database. This eliminates the entire `~/.flowstate/runs/<run-id>/` directory tree — no more filesystem-based inter-agent communication.

## Acceptance Criteria
- [ ] `task_artifacts` table exists with columns: `id`, `task_execution_id` (FK), `name` (TEXT), `content` (TEXT), `content_type` (TEXT), `created_at` (TIMESTAMP)
- [ ] Unique constraint on `(task_execution_id, name)` — one artifact per name per task
- [ ] `TaskArtifactRow` Pydantic model in `models.py`
- [ ] `save_artifact(task_execution_id, name, content, content_type)` — upsert (insert or replace)
- [ ] `get_artifact(task_execution_id, name)` → `TaskArtifactRow | None`
- [ ] `list_artifacts(task_execution_id)` → `list[TaskArtifactRow]`
- [ ] Schema migration applies cleanly on existing databases
- [ ] All existing tests continue to pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/state/schema.sql` — add `task_artifacts` table
- `src/flowstate/state/models.py` — add `TaskArtifactRow`
- `src/flowstate/state/repository.py` — add artifact CRUD methods
- `tests/state/test_repository.py` — add artifact tests

### Key Implementation Details

**Schema (schema.sql):**
```sql
CREATE TABLE IF NOT EXISTS task_artifacts (
    id TEXT PRIMARY KEY,
    task_execution_id TEXT NOT NULL REFERENCES task_executions(id),
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/json',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_execution_id, name)
);
```

**Model (models.py):**
```python
class TaskArtifactRow(BaseModel):
    id: str
    task_execution_id: str
    name: str
    content: str
    content_type: str
    created_at: str
```

**Repository methods (repository.py):**
- `save_artifact()`: Use `INSERT OR REPLACE` for upsert semantics. Generate UUID for `id`.
- `get_artifact()`: `SELECT * FROM task_artifacts WHERE task_execution_id = ? AND name = ?`
- `list_artifacts()`: `SELECT * FROM task_artifacts WHERE task_execution_id = ? ORDER BY created_at`

Well-known artifact names:

Agent-submitted:
- `"decision"` — routing decision (application/json)
- `"summary"` — task output summary (text/markdown)
- `"output"` — cross-flow output (application/json)

Engine-written:
- `"input"` — assembled task prompt (text/markdown)
- `"judge_request"` — judge evaluation prompt (text/markdown)
- `"judge_decision"` — judge's routing decision (application/json)

### Edge Cases
- Duplicate save (same task_execution_id + name): upsert replaces content
- Empty content: allowed (agent may submit empty summary)
- Very large content: TEXT column in SQLite handles up to 1GB; no practical limit
- Foreign key constraint: task_execution_id must exist

## Testing Strategy
- Unit tests in `tests/state/test_repository.py`:
  - Save and retrieve an artifact
  - Upsert overwrites previous content
  - Get non-existent artifact returns None
  - List artifacts returns all for a task
  - Foreign key constraint enforced (invalid task_execution_id)

## E2E Verification Plan

### Verification Steps
1. Start server: `uv run flowstate server`
2. Create a flow run, let a task complete
3. Use the artifact API (SERVER-022) to POST a decision
4. Query the database directly to verify storage

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
