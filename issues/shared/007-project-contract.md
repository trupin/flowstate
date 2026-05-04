# [SHARED-007] Define `Project` contract in `config.py`

## Domain
shared

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-006
- Blocks: SERVER-026, STATE-012, SERVER-027, ENGINE-079, ENGINE-080

## Spec References
- specs.md §13.3 Project Layout

## Summary
Create the typed `Project` dataclass and `resolve_project()` helper in `src/flowstate/config.py`. This is the **single choke-point** every CLI entry point, the FastAPI app factory, the flow registry, and the engine executor go through to obtain project-scoped paths. Once this contract exists, Phase 1's five parallel issues can all proceed independently because they each consume `Project` rather than reading config directly.

## Acceptance Criteria
- [ ] `Project` dataclass is defined in `src/flowstate/config.py`, frozen, with fields: `root: Path`, `slug: str`, `config: FlowstateConfig`, `data_dir: Path`, `flows_dir: Path`, `db_path: Path`, `workspaces_dir: Path`. All `Path` fields are absolute and already resolved.
- [ ] `resolve_project(start: Path | None = None) -> Project` helper walks up from `start` (default: CWD) looking for `flowstate.toml`. Returns a fully-built `Project` on success.
- [ ] `ProjectNotFoundError(FlowstateError)` is raised when no `flowstate.toml` is found up to the filesystem root. Error message references `flowstate init`.
- [ ] `FLOWSTATE_CONFIG` env var, when set, short-circuits the walk-up: the path is treated as an explicit `flowstate.toml` location.
- [ ] `FLOWSTATE_DATA_DIR` env var, when set, overrides `~/.flowstate`. Default remains `~/.flowstate`.
- [ ] Slug derivation: `<basename>-<sha1(str(root.resolve()))[:8]>`. Stable for a given absolute path.
- [ ] `Project` construction ensures `data_dir`, `flows_dir`, and `workspaces_dir` exist (mkdir parents, idempotent). `db_path` parent is created but the file itself is not touched.
- [ ] The old `load_config()` signature is preserved as a thin shim that calls `resolve_project()` and returns `project.config` — or is clearly deprecated and flagged. (Decision: shim is fine, SERVER-026 will migrate callers.)
- [ ] Unit tests in `tests/test_config.py` cover: slug stability, walk-up resolution, `ProjectNotFoundError`, `FLOWSTATE_CONFIG` override, `FLOWSTATE_DATA_DIR` override, nested-project "nearest wins" semantics.

## Technical Design

### Files to Create/Modify
- `src/flowstate/config.py` — add `Project` dataclass, `resolve_project()`, `ProjectNotFoundError`. Keep `FlowstateConfig` for the TOML schema itself.
- `tests/test_config.py` — new or extended test file covering the above cases.

### Key Implementation Details
```python
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from flowstate.errors import FlowstateError  # or a local error type

PROJECT_ANCHOR = "flowstate.toml"
DEFAULT_DATA_DIR = Path.home() / ".flowstate"


class ProjectNotFoundError(FlowstateError):
    """Raised when no flowstate.toml can be found by walking up from CWD."""


@dataclass(frozen=True)
class Project:
    root: Path
    slug: str
    config: FlowstateConfig
    data_dir: Path
    flows_dir: Path
    db_path: Path
    workspaces_dir: Path


def _derive_slug(root: Path) -> str:
    abspath = str(root.resolve())
    digest = hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:8]
    return f"{root.name}-{digest}"


def _find_anchor(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        anchor = candidate / PROJECT_ANCHOR
        if anchor.is_file():
            return anchor
    return None


def resolve_project(start: Path | None = None) -> Project:
    # 1. Explicit override via FLOWSTATE_CONFIG env var
    override = os.environ.get("FLOWSTATE_CONFIG")
    if override:
        anchor = Path(override).expanduser().resolve()
        if not anchor.is_file():
            raise ProjectNotFoundError(
                f"FLOWSTATE_CONFIG={override} does not exist"
            )
    else:
        anchor = _find_anchor(start or Path.cwd())
        if anchor is None:
            raise ProjectNotFoundError(
                "No flowstate.toml found in the current directory or any parent. "
                "Run `flowstate init` to create one."
            )

    root = anchor.parent.resolve()
    config = FlowstateConfig.from_toml(anchor)
    slug = _derive_slug(root)

    data_root = Path(os.environ.get("FLOWSTATE_DATA_DIR") or DEFAULT_DATA_DIR).expanduser()
    data_dir = (data_root / "projects" / slug).resolve()
    flows_dir = (root / config.flows.watch_dir).resolve()
    db_path = data_dir / "flowstate.db"
    workspaces_dir = data_dir / "workspaces"

    data_dir.mkdir(parents=True, exist_ok=True)
    workspaces_dir.mkdir(parents=True, exist_ok=True)
    flows_dir.mkdir(parents=True, exist_ok=True)

    return Project(
        root=root,
        slug=slug,
        config=config,
        data_dir=data_dir,
        flows_dir=flows_dir,
        db_path=db_path,
        workspaces_dir=workspaces_dir,
    )
```

### Edge Cases
- `start` is a file path, not a directory → use its parent.
- CWD is above any project → `ProjectNotFoundError`.
- Nested projects → `_find_anchor` returns the **first** match while walking up, i.e. the nearest ancestor, matching the spec.
- Symlinks → `resolve()` everywhere before hashing or comparing.
- `FLOWSTATE_CONFIG` points at a file whose name isn't `flowstate.toml` → still accepted; the env var is "use this file as the config"; `root` is its parent directory.
- `config.flows.watch_dir` is absolute → `(root / absolute).resolve()` yields the absolute path unchanged (per `pathlib` semantics).

## Testing Strategy
- **Unit tests only** — pure function of filesystem state. Use `tmp_path` fixtures to build synthetic project trees.
- Assert slug stability: two calls on the same path yield the same slug; two paths with the same basename but different parents yield different slugs.
- Assert walk-up: anchor in grandparent is found from a deep CWD.
- Assert nearest-wins: anchor in parent and grandparent → parent wins.
- Assert `FLOWSTATE_CONFIG` override wins even when there would otherwise be a walk-up match.
- Assert `ProjectNotFoundError` when no anchor anywhere in the chain.
- Assert `data_dir`, `flows_dir`, `workspaces_dir` exist after `resolve_project()`.

## E2E Verification Plan
This issue has no runtime UI/server surface on its own — its E2E verification is subsumed by the Phase 1 issues that consume it. However, a smoke test:

### Verification Steps
1. `cd /tmp && rm -rf fs-smoke && mkdir fs-smoke && cd fs-smoke && printf '[flows]\nwatch_dir = "flows"\n' > flowstate.toml`
2. `uv run python -c "from pathlib import Path; from flowstate.config import resolve_project; p = resolve_project(); print(p.slug, p.data_dir, p.flows_dir)"`
3. Expected: prints a slug like `fs-smoke-abcd1234`, a `data_dir` under `~/.flowstate/projects/fs-smoke-*/`, and `flows_dir = /tmp/fs-smoke/flows`.
4. `cd /tmp && uv run python -c "from flowstate.config import resolve_project; resolve_project()"` → raises `ProjectNotFoundError`.

## E2E Verification Log

### Post-Implementation Verification

**Smoke test (spec §13.3 resolution algorithm):**
```
$ cd /tmp && rm -rf fs-smoke && mkdir fs-smoke && cd fs-smoke \
  && printf '[flows]\nwatch_dir = "flows"\n' > flowstate.toml
$ uv --project /Users/theophanerupin/code/flowstate/.claude/worktrees/phase-31-deployability \
    run python -c "from flowstate.config import resolve_project; p = resolve_project(); \
                   print(p.slug, p.data_dir, p.flows_dir)"
fs-smoke-<8hex>  /Users/theophanerupin/.flowstate/projects/fs-smoke-<8hex>  /private/tmp/fs-smoke/flows
```
Walk-up, slug derivation, and directory auto-creation all work as specified.

**Unit tests (13 new tests, all passing):**
- `test_resolve_project_finds_anchor_in_cwd` — baseline walk-up
- `test_resolve_project_walks_up_from_subdirectory` — walk-up from a nested child
- `test_resolve_project_nearest_ancestor_wins` — nested projects: inner wins
- `test_resolve_project_raises_when_no_anchor` — ProjectNotFoundError message references `flowstate init`
- `test_resolve_project_flowstate_config_env_var_overrides_walk_up` — FLOWSTATE_CONFIG short-circuit
- `test_resolve_project_flowstate_config_missing_raises` — bad FLOWSTATE_CONFIG → ProjectNotFoundError
- `test_resolve_project_flowstate_data_dir_override` — FLOWSTATE_DATA_DIR override
- `test_slug_is_stable_for_same_path` — deterministic slug
- `test_slug_differs_for_same_basename_different_paths` — collision safety
- `test_resolve_project_auto_creates_dirs` — data_dir, flows_dir, workspaces_dir mkdir
- `test_resolve_project_with_file_as_start` — start param can be a file path
- `test_project_is_frozen` — FrozenInstanceError on mutation
- `test_load_config_still_works_for_backward_compat` — legacy shim still functions

```
$ uv run pytest tests/server/test_config.py -q
13 passed in 0.03s
```

**Type check:**
```
$ uv run pyright src/flowstate/config.py tests/server/test_config.py
0 errors, 0 warnings, 0 informations
```

**Lint:**
```
$ uv run ruff check src/flowstate/config.py tests/server/test_config.py
All checks passed!
```

**Regression sweep:**
- `tests/state` — 214/214 pass
- `tests/server` — 336 pass; 10 pre-existing failures confirmed on clean main (port==8080 stale assertions, ENGINE-078 pausing-state transition tests, and test_cli integration tests). None caused by this issue.
- `tests/engine` subset (budget, context, events, harness, judge, queue_manager, scheduler, subprocess_mgr, worktree, file_protocol, edge_delays, sdk_runner, lumon, acp_client) — 398/398 pass. Executor tests not re-run (config.py is not a dependency of executor code paths).
- `tests/server/test_app.py::TestDefaultConfig::test_all_defaults` — updated `watch_dir == "./flows"` → `watch_dir == "flows"` to match the new project-rooted default per spec §13.1. Pre-existing stale `server_port == 8080` assertion left alone (not in scope).

The `Project` contract is now the single choke-point all Phase 31.1 issues will consume.

## Completion Checklist
- [x] `Project` dataclass + `resolve_project()` implemented
- [x] `ProjectNotFoundError` defined
- [x] Env-var overrides implemented (`FLOWSTATE_CONFIG`, `FLOWSTATE_DATA_DIR`)
- [x] Unit tests passing (13 new tests)
- [x] `/lint` passes
- [x] Smoke test verified
- [x] `load_config()` shim preserved for SERVER-026 migration
