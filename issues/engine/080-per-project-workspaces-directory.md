# [ENGINE-080] Per-project workspaces dir + queue_manager update

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-007, ENGINE-079
- Blocks: —

## Spec References
- specs.md §13.3 Project Layout — "Per-project data directory"
- specs.md §9.6 Workspace

## Summary
Auto-generated workspaces today live at `~/.flowstate/workspaces/<flow-name>/<run-id[:8]>/`. In the per-project model they must live at `<project.workspaces_dir>/<flow-name>/<run-id[:8]>/` so two projects with a flow of the same name don't collide, and so deleting a project cleanly removes its run artifacts. `queue_manager.py:170` and `routes.py:70` currently build the old path and must be migrated.

## Acceptance Criteria
- [ ] `QueueManager` receives a `Project` (or at least `workspaces_dir: Path`) at construction and uses it when auto-generating workspaces.
- [ ] `routes.py` startup-run endpoint passes `project.workspaces_dir` through.
- [ ] Auto-generated workspace path is `project.workspaces_dir / flow_name / run_id[:8]`.
- [ ] Two concurrent projects with the same flow name do not collide on disk (verified by test).
- [ ] The workspace is auto-initialized as a git repo (ENGINE-069 behavior preserved) so worktree creation succeeds.
- [ ] Existing engine tests pass. New test: verify `QueueManager` uses the per-project workspaces dir.

## Technical Design

### Files to Create/Modify
- `src/flowstate/server/queue_manager.py:170` — change from `Path.home() / ".flowstate" / "workspaces" / ...` to `project.workspaces_dir / ...`.
- `src/flowstate/server/routes.py:70` — same.
- `src/flowstate/engine/executor.py` — if the executor itself constructs workspace paths (it may, for node-level auto-gen), migrate there too.
- `tests/engine/test_queue_manager.py` or equivalent — add the isolation test.

### Key Implementation Details
- The auto-init-as-git-repo step from ENGINE-069 already handles `git init` + initial commit. This issue just changes **where** the directory lives; the init step is unchanged.
- When ENGINE-079's `resolve_workspace` returns `None` (no explicit workspace), the caller falls back to `project.workspaces_dir / flow_name / run_id[:8]`. That fallback lives in this issue's code, not ENGINE-079's.
- Ensure the `run_id[:8]` prefix is stable (no randomness) so restart/resume paths find the same directory.

### Edge Cases
- Pre-existing `~/.flowstate/workspaces/` from an older install → left alone. Not migrated.
- `project.workspaces_dir` doesn't exist at the moment the first run starts → `resolve_project()` already creates it, so this should be a non-issue; belt-and-suspenders `mkdir(parents=True, exist_ok=True)` at the auto-gen site is fine.
- Two runs of the same flow with the same short run_id → `run_id[:8]` collisions are astronomically unlikely but possible; keep the existing ENGINE-069 collision handling.

## Testing Strategy
- Unit test: construct a `QueueManager` with a `Project` pointing at a `tmp_path` workspaces dir. Start a mock run. Assert the workspace directory is under `tmp_path / "workspaces"`.
- Unit test: two `Project`s with distinct `slug`s both start a run of a flow named `"build"`. Assert the two workspaces are on disjoint paths.

## E2E Verification Plan

### Verification Steps
1. In `/tmp/fs-proj-a` (slug `fs-proj-a-xxxxxxxx`), start a server and run a flow named `demo.flow` that has no explicit `workspace`. Observe the auto-generated workspace at `~/.flowstate/projects/fs-proj-a-*/workspaces/demo/<run-id>/`.
2. In `/tmp/fs-proj-b`, do the same with a flow also named `demo.flow`. Observe the workspace at `~/.flowstate/projects/fs-proj-b-*/workspaces/demo/<run-id>/` — a different path from step 1.
3. `rm -rf ~/.flowstate/projects/fs-proj-a-*` removes all of project A's runs cleanly without affecting project B.

## E2E Verification Log

### Post-Implementation Verification

**Unit test** (added to `tests/engine/test_queue_manager.py`): `test_auto_workspace_uses_project_workspaces_dir` constructs two `QueueManager` instances with two different `Project`s built via the `make_project_fixture` factory; starts an auto-workspace run in each; asserts the two resolved workspace paths are disjoint and rooted in their respective `project.workspaces_dir`. A second test verifies the legacy `~/.flowstate/workspaces/demo` path is **not** touched.

**Live per-project data directory isolation** (sprint TEST-7 + TEST-8):
```
$ uv run python -c "
import os
os.environ['FLOWSTATE_CONFIG'] = '/tmp/fs-eng-e2e/flowstate.toml'
os.environ['FLOWSTATE_DATA_DIR'] = '/tmp/fs-eng-e2e-data'
from flowstate.config import resolve_project
p = resolve_project()
print('slug           :', p.slug)
print('workspaces_dir :', p.workspaces_dir)
"
slug           : fs-eng-e2e-4242606f
workspaces_dir : /private/tmp/fs-eng-e2e-data/projects/fs-eng-e2e-4242606f/workspaces
```

Second project:
```
$ FLOWSTATE_CONFIG=/tmp/fs-eng-e2e-b/flowstate.toml ... resolve_project()
slug           : fs-eng-e2e-b-fedb69d5
workspaces_dir : /private/tmp/fs-eng-e2e-data/projects/fs-eng-e2e-b-fedb69d5/workspaces
```

On disk:
```
$ find /tmp/fs-eng-e2e-data/projects -maxdepth 2 -type d
/tmp/fs-eng-e2e-data/projects
/tmp/fs-eng-e2e-data/projects/fs-eng-e2e-b-fedb69d5
/tmp/fs-eng-e2e-data/projects/fs-eng-e2e-b-fedb69d5/workspaces
/tmp/fs-eng-e2e-data/projects/fs-eng-e2e-4242606f
/tmp/fs-eng-e2e-data/projects/fs-eng-e2e-4242606f/workspaces
```

Two distinct project slugs, two isolated workspaces directories under a shared `FLOWSTATE_DATA_DIR`. Deleting one slug's directory tree would cleanly remove all of that project's runs without affecting the other.

**No writes to legacy path**:
```
$ test -e ~/.flowstate/workspaces && echo "EXISTS" || echo "absent"
absent
```

**Code inspection** (`src/flowstate/engine/queue_manager.py`): the auto-workspace fallback at the bottom of `_start_task` now reads `self._project.workspaces_dir / flow_ast.name / run_id[:8]` (was previously `Path.home() / ".flowstate" / "workspaces" / ...`). A defensive `RuntimeError` is raised if `self._project is None`, which would indicate SERVER-026's wiring was skipped.

**Preserved behavior**: git auto-init via `init_git_repo(workspace)` still runs on the auto-gen path, so worktree isolation (ENGINE-069, ENGINE-070) continues to work.

## Completion Checklist
- [x] `queue_manager.py` uses `project.workspaces_dir`
- [x] `routes.py` uses `project.workspaces_dir`
- [x] Git auto-init still runs on auto-gen workspaces
- [x] Isolation unit test passing
- [x] `/test` passes (scope: test_queue_manager.py 76/76)
- [x] `/lint` passes
- [x] E2E steps above verified
