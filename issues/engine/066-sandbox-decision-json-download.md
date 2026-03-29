# [ENGINE-066] Download DECISION.json from sandbox after task completion

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-065
- Blocks: —

## Spec References
- specs.md Section 3.3 — "Flow Declaration" (sandbox behavior)

## Summary
When a sandboxed task completes, the agent writes `DECISION.json` inside the sandbox at `/sandbox/DECISION.json`, but the executor expects it at the host path `~/.flowstate/runs/<id>/tasks/<node>-<gen>/DECISION.json`. Fix by downloading the file from the sandbox after task completion, before routing decision is read.

## Acceptance Criteria
- [ ] After a sandboxed task completes (exit code 0), download `DECISION.json` from the sandbox
- [ ] Download uses `openshell sandbox download <name> /sandbox/DECISION.json <host-task-dir>/DECISION.json`
- [ ] If the download fails (file doesn't exist in sandbox), routing proceeds normally (unconditional edges don't need DECISION.json)
- [ ] The sandbox name is available in the executor's post-task processing
- [ ] Existing non-sandboxed task routing is unchanged

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/sandbox.py` — add `download_file()` method
- `src/flowstate/engine/executor.py` — call download after sandboxed task completes
- `tests/engine/test_sandbox.py` — test download_file
- `tests/engine/test_executor.py` — test post-task download for sandboxed tasks

### Key Implementation Details

**`sandbox.py` — add download method:**
```python
async def download_file(
    self,
    sandbox_path: str,
    host_path: str,
) -> bool:
    """Download a file from the sandbox to the host. Returns True on success."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "openshell", "sandbox", "download",
            self.sandbox_name, sandbox_path, host_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        return proc.returncode == 0
    except OSError:
        return False
```

**`executor.py` — after sandboxed task completes:**
In `_execute_single_task()`, after the task stream ends and before routing, download DECISION.json:
```python
if use_sandbox and exit_code == 0:
    await self._sandbox_mgr.download_file(
        "/sandbox/DECISION.json",
        str(Path(task_exec.task_dir) / "DECISION.json"),
    )
```

The download is best-effort — if the file doesn't exist (unconditional edges), the download fails silently and routing proceeds normally.

### Edge Cases
- Task has no conditional edges → no DECISION.json in sandbox → download fails silently → OK
- Task failed (exit code != 0) → skip download → flow pauses on error
- Download fails due to openshell issue → routing fails with "DECISION.json not found" → clear error

## Testing Strategy
- Unit test download_file with mocked subprocess
- Integration test in executor: verify download called after sandboxed task

## E2E Verification Plan

### Verification Steps
1. Start server, start sandboxed discuss_flowstate flow
2. Moderator task completes → DECISION.json downloaded from sandbox
3. Routing succeeds → next task starts

## E2E Verification Log

### Post-Implementation Verification

**Tests**: All 592 engine tests pass (`uv run pytest tests/engine/ -v` -- 92.62s).

New tests added:
- `tests/engine/test_sandbox.py::TestDownloadFile` -- 5 tests covering success, non-zero exit, OSError, sandbox name, and path arguments.
- `tests/engine/test_executor.py::TestSandboxDecisionDownload` -- 4 tests covering:
  - download_file called for each sandboxed task that exits 0
  - no download for non-sandboxed tasks
  - no download when task fails (exit code != 0)
  - download failure is silent (flow still completes)

**Lint**: `uv run ruff check src/flowstate/engine/ tests/engine/` -- All checks passed.

**Types**: `uv run pyright src/flowstate/engine/` -- 0 errors, 0 warnings, 0 informations.

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
