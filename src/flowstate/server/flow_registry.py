"""Flow discovery registry — scans and watches a directory for .flow files."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from watchfiles import Change, awatch

from flowstate.dsl.parser import parse_flow
from flowstate.dsl.type_checker import check_flow

if TYPE_CHECKING:
    from collections.abc import Callable

    from flowstate.dsl.ast import Flow

logger = logging.getLogger(__name__)


def _serialize_value(obj: Any) -> Any:
    """Recursively convert dataclass/enum/tuple values to JSON-serializable types."""
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize_value(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _serialize_value(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_serialize_value(item) for item in obj]
    return obj


def _serialize_flow(flow_ast: Flow) -> dict[str, Any]:
    """Convert a Flow AST to a JSON-serializable dict."""
    return _serialize_value(flow_ast)


@dataclass
class DiscoveredFlow:
    """An in-memory representation of a discovered .flow file.

    ``flow_file`` is the absolute :class:`Path` to the source file on disk
    (equivalent to ``Path(file_path)``). It is exposed as a ``Path`` so
    downstream consumers — notably the engine's workspace resolver — can
    compute paths relative to the flow file's parent directory without
    re-parsing strings. When not explicitly provided, it is derived from
    ``file_path`` in :meth:`__post_init__`.
    """

    id: str
    name: str | None
    file_path: str
    source_dsl: str
    status: str
    errors: list[str]
    ast_json: dict[str, Any] | None = None
    params: list[dict[str, Any]] = field(default_factory=list)
    flow_file: Path = field(default_factory=lambda: Path())

    def __post_init__(self) -> None:
        # Derive flow_file from file_path when the caller didn't pass one.
        # An empty Path() is the sentinel for "not set".
        if self.flow_file == Path():
            self.flow_file = Path(self.file_path) if self.file_path else Path()


class FlowRegistry:
    """Maintains an in-memory registry of discovered .flow files.

    Scans an absolute flows directory for .flow files on startup, parses and
    type-checks each one, and watches for changes in the background using
    watchfiles. The directory is created on construction if missing so a
    freshly ``flowstate init``-ed project does not race on the first scan.
    """

    def __init__(self, flows_dir: Path) -> None:
        self._flows_dir = Path(flows_dir).expanduser().resolve()
        self._flows_dir.mkdir(parents=True, exist_ok=True)
        self._flows: dict[str, DiscoveredFlow] = {}
        self._watch_task: asyncio.Task[None] | None = None
        self._event_callback: Callable[[str, DiscoveredFlow], None] | None = None

    @property
    def flows_dir(self) -> Path:
        """The absolute, resolved flows directory path."""
        return self._flows_dir

    @property
    def watch_dir(self) -> Path:
        """Alias for :attr:`flows_dir` (legacy name)."""
        return self._flows_dir

    def set_event_callback(self, callback: Callable[[str, DiscoveredFlow], None]) -> None:
        """Set callback for file change events.

        Used by SERVER-006 for WebSocket broadcasting.
        The callback receives (event_type, discovered_flow) where event_type
        is one of "file_changed", "file_error", "file_valid", "file_removed".
        """
        self._event_callback = callback

    async def start(self) -> None:
        """Scan flows_dir and start background file watcher."""
        self._flows_dir.mkdir(parents=True, exist_ok=True)
        self._scan_all()
        self._watch_task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        """Stop the background file watcher."""
        if self._watch_task:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task

    def list_flows(self) -> list[DiscoveredFlow]:
        """Return all discovered flows."""
        return list(self._flows.values())

    def get_flow(self, flow_id: str) -> DiscoveredFlow | None:
        """Return a single flow by ID (file stem), or None if not found."""
        return self._flows.get(flow_id)

    def get_flow_by_name(self, name: str) -> DiscoveredFlow | None:
        """Return a flow by its declared name, or None if not found."""
        for flow in self._flows.values():
            if flow.name == name:
                return flow
        return None

    def _scan_all(self) -> None:
        """Scan flows_dir for all .flow files and process each."""
        for path in sorted(self._flows_dir.glob("*.flow")):
            self._process_file(path)

    def _process_file(self, path: Path) -> None:
        """Parse + type-check a .flow file and update the registry."""
        flow_id = path.stem
        abs_path = path.resolve()
        errors: list[str] = []
        ast_json: dict[str, Any] | None = None
        flow_name: str | None = None
        params: list[dict[str, Any]] = []

        try:
            source = abs_path.read_text()
        except (UnicodeDecodeError, OSError) as e:
            logger.warning("Failed to read %s: %s", abs_path, e)
            self._flows[flow_id] = DiscoveredFlow(
                id=flow_id,
                name=None,
                file_path=str(abs_path),
                flow_file=abs_path,
                source_dsl="",
                status="error",
                errors=[str(e)],
            )
            return

        try:
            flow_ast = parse_flow(source)
            flow_name = flow_ast.name
            type_errors = check_flow(flow_ast)
            if type_errors:
                errors = [str(e) for e in type_errors]
            else:
                ast_json = _serialize_flow(flow_ast)
                params = [
                    {
                        "name": p.name,
                        "type": p.type,
                        "default_value": p.default,
                    }
                    for p in flow_ast.input_fields
                ]
        except Exception as e:
            errors = [str(e)]

        discovered = DiscoveredFlow(
            id=flow_id,
            name=flow_name or flow_id,
            file_path=str(abs_path),
            flow_file=abs_path,
            source_dsl=source,
            status="valid" if not errors else "error",
            errors=errors,
            ast_json=ast_json,
            params=params,
        )
        self._flows[flow_id] = discovered

    def _remove_file(self, path: Path) -> None:
        """Remove a flow from the registry by its file path."""
        flow_id = path.stem
        self._flows.pop(flow_id, None)

    async def _watch_loop(self) -> None:
        """Watch for file changes using watchfiles."""
        async for changes in awatch(self._flows_dir):
            for change_type, path_str in changes:
                path = Path(path_str)
                if path.suffix != ".flow":
                    continue
                if change_type == Change.deleted or (
                    change_type in (Change.added, Change.modified) and not path.exists()
                ):
                    # File was deleted, or reported as modified but no longer exists
                    self._remove_file(path)
                elif change_type in (Change.added, Change.modified):
                    self._process_file(path)
                    if self._event_callback:
                        flow = self._flows.get(path.stem)
                        if flow:
                            event_type = "file_error" if flow.errors else "file_valid"
                            self._event_callback(event_type, flow)
