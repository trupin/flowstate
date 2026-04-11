# [SHARED-008] Hatchling build hook: bundle UI into wheel

## Domain
shared

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-006
- Blocks: SERVER-032, SHARED-010

## Spec References
- specs.md §13.4 Deployment & Installation — "UI packaging"

## Summary
Today the React UI has to be built by hand (`cd ui && npm run build`) and lives at `ui/dist/`, which is mounted by the FastAPI app via a relative path. A pipx-installed user has no `ui/` directory and no Node toolchain. This issue adds a custom Hatchling build hook that runs `npm ci && npm run build` in `ui/` during wheel build and copies the output to `src/flowstate/_ui_dist/`, which is then included in the wheel as package data. End users install a single wheel and get the UI for free.

## Acceptance Criteria
- [ ] A custom Hatchling build hook (either inline in `pyproject.toml` or a `hatch_build.py` at the repo root) runs during wheel build:
  1. `npm ci` in `ui/` (respects `ui/package-lock.json`)
  2. `npm run build` in `ui/`
  3. Copies `ui/dist/*` recursively to `src/flowstate/_ui_dist/`
- [ ] `src/flowstate/_ui_dist/` is `.gitignore`-d but included in the wheel via `[tool.hatch.build.targets.wheel] include` (or `artifacts`).
- [ ] The sdist includes `ui/` source files so building from sdist is possible (users installing from sdist need Node on their machine; documented).
- [ ] `uv build` produces a wheel that, when extracted, contains `flowstate/_ui_dist/index.html` and `flowstate/_ui_dist/assets/*`.
- [ ] Local dev workflow is preserved: `npm run dev` (Vite) and `uv run flowstate server` from the repo root still work. The server must fall back to `ui/dist/` (next to the source) when `_ui_dist/` is empty — this fallback is owned by SERVER-032.
- [ ] CI builds the wheel without Node being pre-installed in the Flowstate dev environment — the build hook installs/runs Node via `npm`, which must be available. (Document this as a build-time prerequisite in `RELEASING.md`.)
- [ ] A smoke test script (`scripts/verify_wheel_ui.sh` or similar) extracts the built wheel, checks for `flowstate/_ui_dist/index.html`, and exits non-zero if missing.

## Technical Design

### Files to Create/Modify
- `pyproject.toml` — register the custom build hook; add wheel artifacts config.
- `hatch_build.py` (new, at repo root) — the hook implementation.
- `.gitignore` — add `src/flowstate/_ui_dist/`.
- `src/flowstate/_ui_dist/.gitkeep` — empty placeholder so the package directory exists in source checkouts (safe to be empty; SERVER-032 handles the empty case).
- `RELEASING.md` — new doc (also updated in SHARED-010) noting that releases require Node 20+.
- `scripts/verify_wheel_ui.sh` — new smoke test.

### Key Implementation Details

**`pyproject.toml`:**
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/flowstate"]
artifacts = [
  "src/flowstate/_ui_dist/**",
]

[tool.hatch.build.targets.sdist]
include = [
  "src/",
  "ui/",
  "pyproject.toml",
  "hatch_build.py",
  "README.md",
  "specs.md",
]

[tool.hatch.build.hooks.custom]
path = "hatch_build.py"
```

**`hatch_build.py`:**
```python
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class UIBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel":
            return

        root = Path(self.root)
        ui = root / "ui"
        dist = ui / "dist"
        out = root / "src" / "flowstate" / "_ui_dist"

        if not ui.exists():
            self.app.display_warning("ui/ not found, skipping UI build")
            return

        self.app.display_info("Building Flowstate UI (npm ci && npm run build)...")
        subprocess.run(["npm", "ci"], cwd=ui, check=True)
        subprocess.run(["npm", "run", "build"], cwd=ui, check=True)

        if not dist.exists():
            raise RuntimeError("ui/dist/ not produced by npm run build")

        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(dist, out)
        self.app.display_info(f"Copied UI build to {out}")
```

### Edge Cases
- No `ui/` directory (e.g., someone builds from a tarball that stripped it) → skip, warn, proceed with an empty `_ui_dist/`. SERVER-032's fallback handles the runtime.
- `npm` not in PATH → hook fails loudly at build time. This is intentional — release builds must have Node.
- `ui/package-lock.json` out of sync with `package.json` → `npm ci` fails, which is the correct signal.
- Dev wheels built with `uv build --wheel` must produce a working wheel without manual `npm run build` first — covered by the hook.

## Testing Strategy
- **Unit test not applicable** (this is build tooling, not runtime code).
- **Build test**: CI runs `uv build` (or `hatch build -t wheel`), then runs `scripts/verify_wheel_ui.sh` to confirm `_ui_dist/index.html` is in the wheel.
- Manual: `uv build && unzip -l dist/flowstate-*.whl | grep _ui_dist` → lists UI assets.

## E2E Verification Plan

### Verification Steps
1. `rm -rf src/flowstate/_ui_dist/* ui/dist && uv build`
2. `unzip -p dist/flowstate-0.1.0-py3-none-any.whl flowstate/_ui_dist/index.html | head` → prints HTML.
3. Install the wheel into a throwaway venv: `uv venv /tmp/fs-wheel-test && /tmp/fs-wheel-test/bin/pip install dist/flowstate-0.1.0-py3-none-any.whl`
4. `find /tmp/fs-wheel-test -name index.html -path "*_ui_dist*"` → finds the installed UI assets.
5. `scripts/verify_wheel_ui.sh dist/flowstate-0.1.0-py3-none-any.whl` → exit 0.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `hatch_build.py` implemented
- [ ] `pyproject.toml` hook + artifacts configured
- [ ] `.gitignore` updated
- [ ] `RELEASING.md` notes Node prerequisite
- [ ] Smoke script exits 0 on a fresh build
- [ ] `uv build` works from a clean tree
- [ ] E2E steps above verified
