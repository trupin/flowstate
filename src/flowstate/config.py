"""Flowstate configuration — TOML loader, project resolution, and defaults."""

import hashlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ANCHOR = "flowstate.toml"


def _default_data_dir() -> Path:
    """Return the root directory for per-project data, honoring FLOWSTATE_DATA_DIR."""
    override = os.environ.get("FLOWSTATE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".flowstate"


class ProjectNotFoundError(Exception):
    """Raised when no flowstate.toml can be found by walking up from CWD."""


@dataclass
class HarnessConfigEntry:
    """Configuration for a named harness backend.

    Each entry maps to a ``[harnesses.<name>]`` section in flowstate.toml.
    The ``command`` field is the executable + args list; ``env`` provides
    optional extra environment variables for the subprocess.
    """

    command: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


@dataclass
class FlowstateConfig:
    """Configuration for the Flowstate server and runtime."""

    server_host: str = "127.0.0.1"
    server_port: int = 9090
    max_concurrent_tasks: int = 4
    default_budget: str = "1h"
    judge_model: str = "sonnet"
    judge_confidence_threshold: float = 0.5
    judge_max_retries: int = 1
    database_path: str = "~/.flowstate/flowstate.db"
    database_wal_mode: bool = True
    watch_dir: str = "flows"
    log_level: str = "info"
    worktree_cleanup: bool = True
    harnesses: dict[str, HarnessConfigEntry] = field(default_factory=dict)


@dataclass(frozen=True)
class Project:
    """Resolved project context.

    Every CLI entry point, the FastAPI app factory, the flow registry, and
    the execution engine consume a `Project` instead of re-deriving paths from
    raw config strings. All `Path` fields are absolute and already `.resolve()`-d.
    """

    root: Path
    """Absolute path to the directory containing ``flowstate.toml``."""

    slug: str
    """``<basename>-<sha1(abspath)[:8]>``. Stable for a given absolute path."""

    config: FlowstateConfig
    """Parsed TOML contents."""

    data_dir: Path
    """``<data_root>/projects/<slug>/``. Created on resolve."""

    flows_dir: Path
    """``(root / config.watch_dir).resolve()``. Created on resolve."""

    db_path: Path
    """``<data_dir>/flowstate.db``. Parent created; file not touched."""

    workspaces_dir: Path
    """``<data_dir>/workspaces/``. Created on resolve."""


def load_config(path: str | None = None) -> FlowstateConfig:
    """Load config from TOML file.

    .. deprecated::
        Prefer :func:`resolve_project` — it walks up from CWD to find a
        ``flowstate.toml`` anchor and returns a fully-built :class:`Project`
        with absolute paths. This helper is retained for backward compatibility
        while callsites migrate.

    Search order: explicit path, ./flowstate.toml, ~/.flowstate/config.toml.
    If no file is found and no explicit path given, returns defaults.
    If an explicit path is given but doesn't exist, raises FileNotFoundError.
    """
    if path is not None:
        return _parse_toml(Path(path))

    local = Path("flowstate.toml")
    if local.exists():
        return _parse_toml(local)

    global_path = Path.home() / ".flowstate" / "config.toml"
    if global_path.exists():
        return _parse_toml(global_path)

    return FlowstateConfig()


def resolve_project(start: Path | None = None) -> Project:
    """Resolve the current Flowstate project.

    Resolution order:

    1. If ``FLOWSTATE_CONFIG`` env var is set, treat it as an explicit path
       to a ``flowstate.toml`` file. The project root is its parent directory.
       If the file does not exist, raise :class:`ProjectNotFoundError`.
    2. Otherwise, walk up from ``start`` (default: CWD) looking for a file
       named ``flowstate.toml``. The nearest ancestor wins, so nested
       projects are allowed.
    3. If no anchor is found by the time the filesystem root is reached,
       raise :class:`ProjectNotFoundError` with a message pointing at
       ``flowstate init``.

    Parameters
    ----------
    start:
        Starting directory for the walk-up. Files are accepted and their
        parent directory is used. Defaults to ``Path.cwd()``.

    Returns
    -------
    Project
        A frozen dataclass with absolute, resolved paths. ``data_dir``,
        ``flows_dir``, and ``workspaces_dir`` are created on disk if missing.
    """
    anchor = _find_anchor(start)
    root = anchor.parent.resolve()
    config = _parse_toml(anchor)
    slug = _derive_slug(root)

    data_dir = (_default_data_dir() / "projects" / slug).resolve()
    flows_dir = (root / config.watch_dir).resolve()
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


def _find_anchor(start: Path | None) -> Path:
    """Locate a ``flowstate.toml`` anchor, honoring FLOWSTATE_CONFIG first."""
    override = os.environ.get("FLOWSTATE_CONFIG")
    if override:
        anchor = Path(override).expanduser().resolve()
        if not anchor.is_file():
            raise ProjectNotFoundError(
                f"FLOWSTATE_CONFIG={override} does not exist or is not a file."
            )
        return anchor

    cursor = (start or Path.cwd()).expanduser().resolve()
    if cursor.is_file():
        cursor = cursor.parent

    for candidate in [cursor, *cursor.parents]:
        anchor = candidate / PROJECT_ANCHOR
        if anchor.is_file():
            return anchor

    raise ProjectNotFoundError(
        f"No {PROJECT_ANCHOR} found in {cursor} or any parent directory.\n"
        f"Run `flowstate init` to create one, or cd into a Flowstate project."
    )


def _derive_slug(root: Path) -> str:
    """Return ``<basename>-<sha1(abspath)[:8]>`` for a project root."""
    abspath = str(root.resolve())
    digest = hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:8]
    return f"{root.name}-{digest}"


def _parse_toml(path: Path) -> FlowstateConfig:
    """Parse a TOML file into FlowstateConfig, using defaults for missing keys."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    kwargs: dict[str, Any] = {}

    # Map nested TOML sections to flat dataclass fields
    server = data.get("server", {})
    if "host" in server:
        kwargs["server_host"] = server["host"]
    if "port" in server:
        kwargs["server_port"] = server["port"]

    execution = data.get("execution", {})
    if "max_concurrent_tasks" in execution:
        kwargs["max_concurrent_tasks"] = execution["max_concurrent_tasks"]
    if "default_budget" in execution:
        kwargs["default_budget"] = execution["default_budget"]
    if "worktree_cleanup" in execution:
        kwargs["worktree_cleanup"] = execution["worktree_cleanup"]

    judge = data.get("judge", {})
    if "model" in judge:
        kwargs["judge_model"] = judge["model"]
    if "confidence_threshold" in judge:
        kwargs["judge_confidence_threshold"] = judge["confidence_threshold"]
    if "max_retries" in judge:
        kwargs["judge_max_retries"] = judge["max_retries"]

    database = data.get("database", {})
    if "path" in database:
        kwargs["database_path"] = database["path"]
    if "wal_mode" in database:
        kwargs["database_wal_mode"] = database["wal_mode"]

    flows = data.get("flows", {})
    if "watch_dir" in flows:
        kwargs["watch_dir"] = flows["watch_dir"]

    logging_section = data.get("logging", {})
    if "level" in logging_section:
        kwargs["log_level"] = logging_section["level"]

    harnesses_raw = data.get("harnesses", {})
    if harnesses_raw:
        harness_configs: dict[str, HarnessConfigEntry] = {}
        for name, entry in harnesses_raw.items():
            harness_configs[name] = HarnessConfigEntry(
                command=entry["command"],
                env=entry.get("env"),
            )
        kwargs["harnesses"] = harness_configs

    return FlowstateConfig(**kwargs)
