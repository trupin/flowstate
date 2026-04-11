"""Shared fixtures for server-domain tests.

The ``project_fixture`` factory is the canonical way to build a
:class:`flowstate.config.Project` for tests. Wave 2 agents (STATE-012,
ENGINE-079, ENGINE-080) consume the same helper so every test in the
phase-31 sprint agrees on how a project is constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from flowstate.config import FlowstateConfig, Project, build_project

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class ProjectFixture:
    """A ready-to-use project plus a handle to its on-disk layout.

    Attributes
    ----------
    project:
        The resolved :class:`Project`, with ``db_path``, ``flows_dir`` and
        ``workspaces_dir`` all pointing under ``tmp_path``.
    root:
        Convenience alias for ``project.root``.
    flows_dir:
        Convenience alias for ``project.flows_dir``.
    data_dir:
        Convenience alias for ``project.data_dir`` (the per-project
        ``~/.flowstate/projects/<slug>/`` stand-in).
    """

    project: Project
    root: Path
    flows_dir: Path
    data_dir: Path


def make_project_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    watch_dir: str = "flows",
    config: FlowstateConfig | None = None,
    write_anchor: bool = True,
) -> ProjectFixture:
    """Build a throwaway project rooted under ``tmp_path``.

    Every server/engine/state test that needs a real :class:`Project` should
    call this helper (via the ``project_fixture`` fixture below) so on-disk
    paths are deterministic and isolated per test. The helper:

    - monkeypatches ``FLOWSTATE_DATA_DIR`` so ``~/.flowstate`` is never
      touched,
    - writes a minimal ``flowstate.toml`` at ``tmp_path / "project"``,
    - builds a :class:`Project` via :func:`flowstate.config.build_project`,
      which creates ``data_dir``, ``workspaces_dir`` and ``flows_dir``.
    """
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    if write_anchor:
        anchor = project_root / "flowstate.toml"
        if not anchor.exists():
            anchor.write_text(f'[flows]\nwatch_dir = "{watch_dir}"\n')

    cfg = config if config is not None else FlowstateConfig(watch_dir=watch_dir)
    project = build_project(project_root, cfg)

    return ProjectFixture(
        project=project,
        root=project.root,
        flows_dir=project.flows_dir,
        data_dir=project.data_dir,
    )


@pytest.fixture
def project_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ProjectFixture:
    """Default server-test project: ``flows/`` under a tmp root."""
    return make_project_fixture(tmp_path, monkeypatch)
