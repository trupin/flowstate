# [SERVER-027] Flow registry: resolve `watch_dir` relative to project root

## Domain
server

## Status
done

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
- [x] `FlowRegistry.__init__` takes `flows_dir: Path` (absolute). The class no longer knows anything about CWD or the `watch_dir` string.
- [x] All callers pass `project.flows_dir` (`create_app` wires `FlowRegistry(flows_dir=project.flows_dir)` in `lifespan()`).
- [x] `FlowRegistry` creates `flows_dir` if it doesn't exist (parents=True, idempotent) so a freshly `flowstate init`-ed project doesn't race on the first scan — mkdir runs both in `__init__` and at the top of `start()`.
- [x] `watchfiles` is watching the absolute path (`awatch(self._flows_dir)`); change events still fire correctly after the switch (verified via TEST-4 E2E drop-in).
- [x] Existing tests pass; added `TestFlowsDirAbsolutePath` with two new cases in `tests/server/test_flow_discovery.py`:
    - a test that uses a `project_fixture`'s `flows_dir`, cds to an unrelated directory, and verifies the registry still discovers the flow and exposes an absolute `flow_file: Path` on `DiscoveredFlow`.
    - a test that constructs a registry twice against a missing nested path to prove idempotent mkdir.
- [x] **(Additive for ENGINE-079 unblock)** `DiscoveredFlow` now exposes `flow_file: Path` alongside the existing `file_path: str`. `__post_init__` derives it from `file_path` when not explicitly provided, so existing test fixtures that construct `DiscoveredFlow(...)` keep working unchanged.

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

### Post-Implementation Verification (2026-04-11)

These steps exercise the same running servers used for SERVER-026 (see
that issue's log for server-start commands). They focus on the two
SERVER-027 acceptance paths: (a) absolute `flows_dir` wiring from
`project.flows_dir`, and (b) absolute `flow_file` paths on the API.

**Step 1 — dev-repo registry picks up committed flows**

```
$ curl -s http://127.0.0.1:9090/api/flows | head -c 200
[{
    edges:
    [{
        ...
```

The three committed flows under `flows/` are surfaced with absolute
`file_path` values rooted at the worktree's `flows/` directory.

**Step 2 — scratch project's flow_file is absolute and rooted in project.flows_dir**

```
$ cat > /tmp/fs-sprint-26/flows/demo.flow <<'EOF'
flow demo { ... }
EOF
$ sleep 2 && curl -s http://127.0.0.1:9092/api/flows | head -c 200
[{"id":"demo","name":"demo","file_path":"/private/tmp/fs-sprint-26/flows/demo.flow",...
```

`file_path` is the absolute path under the scratch project's
`flows/` directory — confirming `FlowRegistry(project.flows_dir)`
is doing the discovery, not a stale CWD-relative `./flows` lookup.

**Step 3 — idempotent mkdir is unit-tested**

`tests/server/test_flow_discovery.py::TestFlowsDirAbsolutePath::test_registry_creates_flows_dir_idempotently`
constructs `FlowRegistry(flows_dir=...)` twice against a missing
nested path and asserts the directory is created and no error is
raised on the second construction. Passing.

**Step 4 — CWD-independence test**

`tests/server/test_flow_discovery.py::TestFlowsDirAbsolutePath::test_registry_accepts_absolute_project_flows_dir`
builds a `project_fixture`, writes a flow under `project.flows_dir`,
`monkeypatch.chdir`s to an unrelated directory, and asserts the
registry still discovers the flow and exposes a `flow_file` that is
absolute and rooted at the project's `flows_dir`. Passing.

## Completion Checklist
- [x] `FlowRegistry` takes absolute `flows_dir: Path`
- [x] Callers updated (`create_app` lifespan)
- [x] Auto-mkdir on construction (and again in `start()`)
- [x] Tests updated and passing (16 existing `FlowRegistry(watch_dir=...)` sites migrated to `FlowRegistry(flows_dir=...)`; 2 new tests added)
- [x] `/lint` passes (`ruff check src/flowstate/ tests/server/` clean)
- [x] E2E steps above verified
- [x] **(Additive)** `DiscoveredFlow.flow_file: Path` exposed for ENGINE-079 consumption
