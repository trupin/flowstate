# [ENGINE-079] Resolve flow `workspace` relative to flow file (fallback: project root)

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-007
- Blocks: ENGINE-080

## Spec References
- specs.md §13.3 Project Layout — "Path resolution within a project"
- specs.md §9.1 Execution Context

## Summary
`src/flowstate/engine/context.py:201-211` currently resolves a flow-level `workspace` attribute relative to CWD. This breaks the moment Flowstate is run as a pipx-installed binary from an arbitrary directory: "CWD" is whatever the user happened to `cd` into, not anything meaningful about the flow. The correct rule (per spec §13.3) is:

1. If the path is absolute → use as-is.
2. If relative → resolve relative to the **flow file's containing directory** (so `workspace = "../backend"` works when the `.flow` file is at `flows/build.flow`, yielding `<project_root>/backend`).
3. If omitted → fall back to an auto-generated workspace under `<project.workspaces_dir>/<flow-name>/<run-id[:8]>/` (this fallback is actually wired in ENGINE-080).

This issue implements rule (1) and (2). Node-level `cwd` follows the same rules.

## Acceptance Criteria
- [ ] Flow-level `workspace` attribute is resolved per the three-rule algorithm above.
- [ ] Node-level `cwd` attribute is resolved identically (absolute → as-is; relative → relative to the flow file's directory).
- [ ] The resolver has access to the flow file path (plumbed in from `FlowRegistry` / `Project`).
- [ ] `CwdResolutionError` is raised with a clear message when the resolved path does not exist and is not an auto-generated workspace.
- [ ] Existing engine tests pass. New unit tests cover: absolute workspace, relative workspace (up one directory), relative node cwd, flow file under a subdirectory.
- [ ] The `project` context is available to the executor (wire a `project: Project` parameter through `create_app` → `QueueManager` → `Executor` → `ExecutionContext`).

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/context.py` — `_resolve_cwd` (or the equivalent) now takes `flow_file_dir: Path` and the `Project` for fallback.
- `src/flowstate/engine/executor.py` and/or `src/flowstate/engine/queue_manager.py` — thread the `Project` and the flow file path through to `ExecutionContext`.
- `src/flowstate/server/flow_registry.py` — expose the flow file's absolute path on the `RegisteredFlow` record (if not already present) so the executor has it.
- `tests/engine/test_context.py` — new unit tests for the four cases above.

### Key Implementation Details
```python
def resolve_workspace(
    flow_workspace: str | None,
    flow_file: Path,
    project_root: Path,
) -> Path | None:
    """Return the resolved absolute workspace path, or None if omitted."""
    if flow_workspace is None:
        return None
    path = Path(flow_workspace).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (flow_file.parent / path).resolve()
```

The same helper handles node-level `cwd`:
```python
def resolve_node_cwd(
    node_cwd: str | None,
    flow_file: Path,
    flow_workspace: Path | None,
) -> Path | None:
    if node_cwd is None:
        return flow_workspace
    path = Path(node_cwd).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (flow_file.parent / path).resolve()
```

The auto-generated fallback (when both `flow_workspace` and `node_cwd` are `None`) is delegated to ENGINE-080, which knows about `project.workspaces_dir`.

### Edge Cases
- Path exists but is not a directory → raise `CwdResolutionError`.
- Relative path escapes the project root (`workspace = "../../other-repo"`) → allowed, but must exist; document this as a "you know what you're doing" case.
- Flow file stored outside the project's `flows/` directory (should not happen but be robust) → still works because we use the flow file's own parent directory.
- Executor receives an `ExecutionContext` that was built before the flow file path was available → refactor to require it at `Executor.run()` time.

## Testing Strategy
- Four new unit tests in `tests/engine/test_context.py` for the cases above, plus a regression for the old CWD-relative behavior to ensure it's gone.
- Update any engine integration tests that relied on CWD-relative workspace to build synthetic project trees.

## E2E Verification Plan

### Verification Steps
1. In a scratch project at `/tmp/fs-ws/`, create `flows/demo.flow` with `workspace = "../target"`. Create `/tmp/fs-ws/target` as a git repo.
2. Start `flowstate server` from `/tmp/fs-ws`.
3. Trigger the flow. Observe the engine resolves the workspace to `/tmp/fs-ws/target` (absolute), not `<CWD>/target`.
4. Run `flowstate server` from a different CWD (e.g., `cd / && FLOWSTATE_CONFIG=/tmp/fs-ws/flowstate.toml flowstate server`) and repeat — the resolved workspace is still `/tmp/fs-ws/target`.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `resolve_workspace` / `resolve_node_cwd` helpers implemented
- [ ] `Project` threaded through to `ExecutionContext`
- [ ] Flow file path available on `RegisteredFlow`
- [ ] Unit tests added and passing
- [ ] `/test` passes
- [ ] `/lint` passes
- [ ] E2E verification recorded in the log
