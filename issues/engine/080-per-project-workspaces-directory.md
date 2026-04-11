# [ENGINE-080] Per-project workspaces dir + queue_manager update

## Domain
engine

## Status
todo

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
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `queue_manager.py` uses `project.workspaces_dir`
- [ ] `routes.py` uses `project.workspaces_dir`
- [ ] Git auto-init still runs on auto-gen workspaces
- [ ] Isolation unit test passing
- [ ] `/test` passes
- [ ] `/lint` passes
- [ ] E2E steps above verified
