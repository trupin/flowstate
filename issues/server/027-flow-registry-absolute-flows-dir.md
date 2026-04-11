# [SERVER-027] Flow registry: resolve `watch_dir` relative to project root

## Domain
server

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-007
- Blocks: SERVER-028

## Spec References
- specs.md §13.3 Project Layout

## Summary
`FlowRegistry` currently takes `watch_dir: str` (default `"./flows"`) and resolves it relative to CWD. Once the server is installed via pipx and run from an arbitrary user project, "CWD-relative" stops making sense. This issue changes `FlowRegistry` to take an absolute `flows_dir: Path` that callers derive from `project.flows_dir`.

## Acceptance Criteria
- [ ] `FlowRegistry.__init__` takes `flows_dir: Path` (absolute). The class no longer knows anything about CWD or the `watch_dir` string.
- [ ] All callers pass `project.flows_dir`.
- [ ] `FlowRegistry` creates `flows_dir` if it doesn't exist (parents=True, idempotent) so a freshly `flowstate init`-ed project doesn't race on the first scan.
- [ ] `watchfiles` is watching the absolute path; change events still fire correctly after the switch.
- [ ] Existing tests pass; add a test that uses a `tmp_path / "flows"` directory and asserts flow discovery works.

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/flow_registry.py` — change constructor signature and the `_scan` / glob logic to operate on the absolute path.
- `src/flowstate/server/app.py` — pass `project.flows_dir` when constructing the registry.
- `tests/server/test_flow_registry.py` — update fixtures; add the absolute-path test.

### Key Implementation Details
- Existing code around `flow_registry.py:117` uses `Path(self.watch_dir).glob("*.flow")`. Replace with `self.flows_dir.glob("*.flow")`.
- `watchfiles.awatch(str(self.flows_dir))` for the file watcher — it already supports absolute paths.
- Delete the `watch_dir: str` field from `FlowstateConfig.flows` OR keep it only as the TOML input (still `"flows"` as a string in TOML) and let `resolve_project()` convert it to the absolute `project.flows_dir`. (Recommended: keep the TOML field as a string, convert once in `resolve_project()`, then never pass it around as a string again.)

### Edge Cases
- `flows_dir` doesn't exist at startup → create it, proceed with an empty registry.
- User deletes and recreates `flows_dir` at runtime → `watchfiles` handles this via the `rust_timeout` / retries it already does; no new logic needed.
- `flows_dir` is a symlink → `resolve()` in SHARED-007 already collapses it.

## Testing Strategy
- Update existing registry tests to build a `tmp_path / "flows"` dir, seed it with `.flow` files, and verify discovery.
- Assert the registry's idempotent mkdir: pointing it at a non-existent path does not error.

## E2E Verification Plan

### Verification Steps
1. `cd /tmp && rm -rf fs-flows && mkdir fs-flows && cd fs-flows && printf '[flows]\nwatch_dir = "flows"\n' > flowstate.toml`
2. `flowstate server` (in one terminal)
3. `touch flows/example.flow` with a trivial valid flow
4. Observe `GET /api/flows` picks up the new flow within the watcher debounce window.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `FlowRegistry` takes absolute `flows_dir: Path`
- [ ] Callers updated
- [ ] Auto-mkdir on construction
- [ ] Tests updated and passing
- [ ] `/lint` passes
- [ ] E2E steps above verified
