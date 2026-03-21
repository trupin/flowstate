"""Flowstate configuration — TOML loader and defaults."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FlowstateConfig:
    """Configuration for the Flowstate server and runtime."""

    server_host: str = "127.0.0.1"
    server_port: int = 8080
    max_concurrent_tasks: int = 4
    default_budget: str = "1h"
    judge_model: str = "sonnet"
    judge_confidence_threshold: float = 0.5
    judge_max_retries: int = 1
    database_path: str = "~/.flowstate/flowstate.db"
    database_wal_mode: bool = True
    watch_dir: str = "./flows"
    log_level: str = "info"
    worktree_cleanup: bool = True


def load_config(path: str | None = None) -> FlowstateConfig:
    """Load config from TOML file.

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

    return FlowstateConfig(**kwargs)
