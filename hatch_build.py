"""Custom Hatchling build hook that bundles the React UI into the wheel.

Runs during ``uv build`` / ``hatch build -t wheel``. The hook is a no-op for
sdist builds — the sdist ships ``ui/`` sources and any downstream consumer
building a wheel from sdist will trigger the hook themselves.

Behavior per SHARED-008:

1. Skip entirely if the target is not ``wheel``.
2. Skip entirely if the environment variable ``FLOWSTATE_SKIP_UI_BUILD=1``
   is set. This is a developer escape hatch for fast local iteration; the
   resulting wheel is **not** shippable (warn loudly, continue).
3. Otherwise run ``npm ci && npm run build`` inside ``ui/`` and copy the
   resulting ``ui/dist/*`` tree to ``src/flowstate/_ui_dist/``. The target
   directory is rewritten on each build.
4. Fail loudly if Node/npm is missing or the build produces no ``ui/dist``.
   A silent UI-less wheel would poison PyPI and we explicitly do not want
   that — see sprint-phase-31-3 TEST-8.5.

The Hatchling wheel target already includes ``src/flowstate/**`` as package
data (via ``[tool.hatch.build.targets.wheel] packages = ["src/flowstate"]``
in pyproject.toml), so ``_ui_dist/`` ships automatically once it exists on
disk. No extra ``artifacts = ...`` entry is required.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class UIBuildHook(BuildHookInterface):  # type: ignore[misc]
    """Build the React UI and copy it into the wheel's package data."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # Only run for wheel builds — sdist should ship ui/ sources as-is.
        if self.target_name != "wheel":
            return

        root = Path(self.root)
        ui_dir = root / "ui"
        dist_dir = ui_dir / "dist"
        out_dir = root / "src" / "flowstate" / "_ui_dist"

        # Developer escape hatch: skip the UI build entirely.
        if os.environ.get("FLOWSTATE_SKIP_UI_BUILD") == "1":
            self.app.display_warning(
                "FLOWSTATE_SKIP_UI_BUILD=1 set — skipping UI build. "
                "The resulting wheel will NOT contain a built UI and is "
                "not suitable for publishing. Unset the env var and rebuild "
                "for a shippable wheel."
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / ".empty").write_text(
                "This wheel was built with FLOWSTATE_SKIP_UI_BUILD=1 and "
                "contains no bundled UI. Do not ship.\n"
            )
            return

        if not ui_dir.is_dir():
            raise RuntimeError(
                f"Flowstate build hook: ui/ directory not found at {ui_dir}. "
                "Cannot build the UI bundle. Install Node >=20 and clone the "
                "full repository before running `uv build`."
            )

        # Require npm on PATH.
        if shutil.which("npm") is None:
            raise RuntimeError(
                "Flowstate build hook: `npm` not found on PATH. "
                "Install Node >=20 (https://nodejs.org) and retry. "
                "If you are iterating locally and do not need the UI in "
                "this wheel, set FLOWSTATE_SKIP_UI_BUILD=1 to skip."
            )

        self.app.display_info("Installing UI dependencies (npm ci)...")
        subprocess.run(["npm", "ci"], cwd=ui_dir, check=True)

        self.app.display_info("Building UI bundle (npm run build)...")
        subprocess.run(["npm", "run", "build"], cwd=ui_dir, check=True)

        if not (dist_dir / "index.html").is_file():
            raise RuntimeError(
                f"Flowstate build hook: `npm run build` finished but "
                f"{dist_dir}/index.html was not produced. Aborting — a wheel "
                "without a built UI must not be shipped."
            )

        # Mirror ui/dist/* into src/flowstate/_ui_dist/ so the wheel picks
        # it up as package data.
        if out_dir.exists():
            shutil.rmtree(out_dir)
        shutil.copytree(dist_dir, out_dir)
        self.app.display_info(f"Copied UI bundle to {out_dir}")

        # Hatchling enumerates package files BEFORE the build hook runs, so
        # files we create in _ui_dist/ won't be auto-included. Register them
        # explicitly via build_data["force_include"], which maps source paths
        # to their destination inside the wheel.
        force_include = build_data.setdefault("force_include", {})
        for src_path in out_dir.rglob("*"):
            if src_path.is_file():
                rel = src_path.relative_to(root / "src")
                force_include[str(src_path)] = str(rel)
