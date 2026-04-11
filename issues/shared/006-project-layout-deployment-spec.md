# [SHARED-006] Spec: project layout & deployment model

## Domain
shared

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: SHARED-007, SERVER-026, STATE-012, SERVER-027, ENGINE-079, ENGINE-080, SERVER-028, SERVER-029, SERVER-030, SERVER-031, SHARED-008, SERVER-032, SHARED-009, SHARED-010, SHARED-011

## Spec References
- specs.md §13.3 Project Layout (stub to be completed by this issue)
- specs.md §13.4 Deployment & Installation (stub to be completed by this issue)

## Summary
Before any code moves, this issue writes the authoritative v0.1 deployability spec. Flowstate today is a dev-repo-only tool; we are redefining it as a per-project dev server installed via `pipx` / `uv tool install`. Every other issue in this batch depends on the spec to be clear about the **project resolution algorithm**, **data directory layout**, **slug derivation**, **UI packaging strategy**, and **security posture**. Without a single source of truth, five domain agents will make five inconsistent guesses.

## Acceptance Criteria
- [ ] `specs.md §13.3 Project Layout` is fully written (no TBD markers), covering: the `flowstate.toml` anchor file, the walk-up resolution algorithm, the `Project` context object's fields, the `<slug> = <basename>-<sha1(abspath)[:8]>` rule, the `~/.flowstate/projects/<slug>/` data directory, and the `FLOWSTATE_DATA_DIR` / `FLOWSTATE_CONFIG` env var overrides.
- [ ] `specs.md §13.4 Deployment & Installation` is fully written (no TBD markers), covering: install channel (`pipx` / `uv tool install`), `flowstate init` bootstrap contract, UI packaging via Hatchling build hook, lumon as an optional extra, and the localhost-only security default.
- [ ] §13.1 `flowstate.toml` schema is updated so all fields are defined in terms of the project root (e.g., `watch_dir = "flows"` instead of `"./flows"`), and the old "global `~/.flowstate/config.toml`" search path is removed from the spec.
- [ ] A short **migration note** is included: users with existing `~/.flowstate/flowstate.db` data are told that v0.1 is greenfield per-project and old data is not migrated.
- [ ] A **path resolution rules** subsection explicitly states how a flow's `workspace` attribute resolves: absolute → as-is; relative → relative to the flow file's containing directory; omitted → auto-generated under `<data_dir>/workspaces/`.
- [ ] Non-goals are enumerated: no Docker, no auth, no global daemon, no migration.

## Technical Design

### Files to Create/Modify
- `specs.md` — replace the TBD stubs in §13.3 / §13.4 (added by the plan-approval turn) with full content. Update §13.1 config schema to match the new resolution rules. Update §13.2 CLI to document `flowstate init`.

### Key Implementation Details
This issue is purely a spec write-up. The orchestrator (not a domain agent) handles it because it's cross-domain. Key decisions that must appear in the spec verbatim so downstream issues can cite them:

1. **Project anchor**: `flowstate.toml` at the project root. Resolution walks up from CWD.
2. **Slug formula**: `<project-basename>-<sha1(str(Path(root).resolve()))[:8]>`. Stable for a given absolute path, collision-safe.
3. **Data dir**: `~/.flowstate/projects/<slug>/` — contains `flowstate.db` and `workspaces/`. Can be relocated via `FLOWSTATE_DATA_DIR`.
4. **Config override**: `FLOWSTATE_CONFIG` env var points at an explicit `flowstate.toml`, short-circuiting the walk-up.
5. **`Project` context object** (authored in SHARED-007): `root`, `slug`, `config`, `data_dir`, `flows_dir`, `db_path`, `workspaces_dir` — all absolute paths.
6. **Flow `workspace` resolution**: absolute → as-is; relative → relative to flow file's containing directory; omitted → `<data_dir>/workspaces/<flow-name>/<run-id[:8]>/`, auto-initialized as a git repo (ENGINE-069 behavior preserved).
7. **UI packaging**: custom Hatchling build hook runs `npm ci && npm run build` in `ui/` during wheel build, copies `ui/dist/*` → `src/flowstate/_ui_dist/`. The runtime serves from `importlib.resources`.
8. **Lumon**: optional extra `flowstate[lumon]`. Core wheel has zero git dependencies.
9. **Default bind**: `127.0.0.1:9090`. Non-loopback bind prints a loud warning to stderr. No auth.

### Edge Cases
- Walk-up hits filesystem root → `ProjectNotFoundError` with a message pointing at `flowstate init`.
- Symlinked project directories → always `Path.resolve()` before hashing for the slug so symlinked aliases collapse to one project.
- Nested `flowstate.toml` files (project inside project) → the **nearest** ancestor wins; document this explicitly.
- User sets `FLOWSTATE_CONFIG` pointing at a non-existent file → fail loudly at load time.

## Testing Strategy
Not applicable — spec changes are validated by the downstream implementation issues successfully citing this section without ambiguity. Cross-check: grep the repo for `[TBD: SHARED-006]` after the issue is complete; there should be zero matches.

## E2E Verification Plan
Not applicable — no runtime behavior.

### Verification Steps
1. `grep -rn "TBD: SHARED-006" specs.md` → no matches.
2. `grep -rn "TBD: SHARED-" specs.md` → only matches pointing at other downstream issues, not SHARED-006.
3. Read §13.3 and §13.4 end-to-end with fresh eyes: can a domain agent implement SERVER-026 / ENGINE-079 / SHARED-008 without asking a clarifying question? If yes, PASS.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] §13.3 Project Layout fully authored
- [ ] §13.4 Deployment & Installation fully authored
- [ ] §13.1 config schema updated to match
- [ ] No `[TBD: SHARED-006]` markers remain
- [ ] Migration note included
- [ ] Non-goals enumerated
