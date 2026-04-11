"""Tests for flowstate.config.resolve_project and the Project dataclass."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from flowstate.config import (
    PROJECT_ANCHOR,
    FlowstateConfig,
    Project,
    ProjectNotFoundError,
    _derive_slug,
    resolve_project,
)


def _write_anchor(root: Path, body: str = "") -> Path:
    anchor = root / PROJECT_ANCHOR
    anchor.write_text(body)
    return anchor


def test_resolve_project_finds_anchor_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

    project_root = tmp_path / "proj"
    project_root.mkdir()
    _write_anchor(project_root, '[flows]\nwatch_dir = "flows"\n')

    project = resolve_project(project_root)

    assert isinstance(project, Project)
    assert project.root == project_root.resolve()
    assert project.slug.startswith("proj-")
    assert len(project.slug.split("-")[-1]) == 8
    assert project.config.watch_dir == "flows"
    assert project.data_dir.is_dir()
    assert project.flows_dir.is_dir()
    assert project.workspaces_dir.is_dir()
    assert project.db_path.parent == project.data_dir
    assert not project.db_path.exists()  # file not touched


def test_resolve_project_walks_up_from_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

    project_root = tmp_path / "proj"
    project_root.mkdir()
    _write_anchor(project_root)

    deep = project_root / "a" / "b" / "c"
    deep.mkdir(parents=True)

    project = resolve_project(deep)
    assert project.root == project_root.resolve()


def test_resolve_project_nearest_ancestor_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

    outer = tmp_path / "outer"
    inner = outer / "nested"
    inner.mkdir(parents=True)
    _write_anchor(outer)
    _write_anchor(inner)

    project = resolve_project(inner / "deep")
    (inner / "deep").mkdir()
    project = resolve_project(inner / "deep")
    assert project.root == inner.resolve()


def test_resolve_project_raises_when_no_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

    # tmp_path has no flowstate.toml anywhere in its chain (pytest owns it).
    # But an ancestor might have one (unlikely in CI). Use a fresh subdir and
    # assert the error mentions flowstate init.
    empty = tmp_path / "empty"
    empty.mkdir()

    # Walk up from `empty`. If pytest's tmp_path chain has no anchor, this raises.
    # If it does (extremely unlikely), skip.
    try:
        resolve_project(empty)
    except ProjectNotFoundError as exc:
        assert "flowstate init" in str(exc)
    else:
        pytest.skip("ancestor of tmp_path contains a flowstate.toml — can't test miss")


def test_resolve_project_flowstate_config_env_var_overrides_walk_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))

    # Project A has an anchor in the walk-up path; project B is the override.
    a_root = tmp_path / "project_a"
    a_root.mkdir()
    _write_anchor(a_root, '[flows]\nwatch_dir = "flows_a"\n')

    b_root = tmp_path / "project_b"
    b_root.mkdir()
    b_anchor = _write_anchor(b_root, '[flows]\nwatch_dir = "flows_b"\n')

    monkeypatch.setenv("FLOWSTATE_CONFIG", str(b_anchor))

    project = resolve_project(a_root)  # start inside A
    assert project.root == b_root.resolve()
    assert project.config.watch_dir == "flows_b"


def test_resolve_project_flowstate_config_missing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.setenv("FLOWSTATE_CONFIG", str(tmp_path / "does-not-exist.toml"))

    with pytest.raises(ProjectNotFoundError, match="FLOWSTATE_CONFIG"):
        resolve_project()


def test_resolve_project_flowstate_data_dir_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "custom-data"
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(data_root))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

    project_root = tmp_path / "proj"
    project_root.mkdir()
    _write_anchor(project_root)

    project = resolve_project(project_root)
    assert (
        data_root.resolve() in project.data_dir.parents
        or project.data_dir == (data_root / "projects" / project.slug).resolve()
    )
    assert project.data_dir.parent.parent == data_root.resolve()


def test_slug_is_stable_for_same_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    s1 = _derive_slug(root)
    s2 = _derive_slug(root)
    assert s1 == s2


def test_slug_differs_for_same_basename_different_paths(tmp_path: Path) -> None:
    a = tmp_path / "a" / "repo"
    b = tmp_path / "b" / "repo"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert _derive_slug(a) != _derive_slug(b)
    assert _derive_slug(a).startswith("repo-")
    assert _derive_slug(b).startswith("repo-")


def test_resolve_project_auto_creates_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

    project_root = tmp_path / "proj"
    project_root.mkdir()
    _write_anchor(project_root, '[flows]\nwatch_dir = "flows"\n')

    # No flows/ directory yet.
    assert not (project_root / "flows").exists()

    project = resolve_project(project_root)

    assert project.data_dir.is_dir()
    assert project.workspaces_dir.is_dir()
    assert project.flows_dir.is_dir()
    assert project.flows_dir == (project_root / "flows").resolve()


def test_resolve_project_with_file_as_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

    project_root = tmp_path / "proj"
    project_root.mkdir()
    _write_anchor(project_root)

    some_file = project_root / "some.txt"
    some_file.write_text("hello")

    project = resolve_project(some_file)
    assert project.root == project_root.resolve()


def test_project_is_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(tmp_path / "fs-data"))
    monkeypatch.delenv("FLOWSTATE_CONFIG", raising=False)

    root = tmp_path / "proj"
    root.mkdir()
    _write_anchor(root)
    project = resolve_project(root)

    with pytest.raises(FrozenInstanceError):
        project.slug = "mutated"  # type: ignore[misc]


def test_load_config_still_works_for_backward_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deprecated load_config() shim must continue to return FlowstateConfig."""
    from flowstate.config import load_config

    toml_path = tmp_path / "flowstate.toml"
    toml_path.write_text('[server]\nhost = "1.2.3.4"\nport = 1234\n')

    cfg = load_config(str(toml_path))
    assert isinstance(cfg, FlowstateConfig)
    assert cfg.server_host == "1.2.3.4"
    assert cfg.server_port == 1234
