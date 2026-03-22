"""Tests for flow discovery: FlowRegistry and REST endpoints."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.server.app import create_app
from flowstate.server.flow_registry import DiscoveredFlow, FlowRegistry

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_for(
    predicate: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.2,
) -> None:
    """Poll until predicate returns True, or raise after timeout."""
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Condition not met within {timeout}s")


VALID_FLOW = """\
flow test_flow {
    budget = 10m
    on_error = pause
    context = handoff
    workspace = "./ws"

    entry start {
        prompt = "hello"
    }

    exit done {
        prompt = "bye"
    }

    start -> done
}
"""

VALID_FLOW_2 = """\
flow another_flow {
    budget = 5m
    on_error = abort
    context = none
    workspace = "./ws2"

    entry begin {
        prompt = "begin"
    }

    exit finish {
        prompt = "finish"
    }

    begin -> finish
}
"""

VALID_FLOW_WITH_PARAMS = """\
flow parameterized {
    budget = 1h
    on_error = pause
    context = handoff
    workspace = "."

    input {
        focus: string = "all"
        verbose: bool = false
    }

    entry start {
        prompt = "go"
    }

    exit done {
        prompt = "done"
    }

    start -> done
}
"""

INVALID_FLOW = "this is not valid flow DSL at all"

FLOW_WITH_TYPE_ERRORS = """\
flow broken {
    budget = 10m
    on_error = pause
    context = handoff
    workspace = "."

    entry start {
        prompt = "hello"
    }

    start -> nonexistent
}
"""


# ---------------------------------------------------------------------------
# FlowRegistry unit tests
# ---------------------------------------------------------------------------


class TestScanDiscoversFlowFiles:
    async def test_scan_discovers_flow_files(self, tmp_path: Path) -> None:
        """Create a tmp dir with two valid .flow files. Verify list_flows returns two valid entries."""
        (tmp_path / "flow_a.flow").write_text(VALID_FLOW)
        (tmp_path / "flow_b.flow").write_text(VALID_FLOW_2)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flows = registry.list_flows()
            assert len(flows) == 2
            ids = {f.id for f in flows}
            assert ids == {"flow_a", "flow_b"}
            for f in flows:
                assert f.status == "valid"
                assert f.errors == []
        finally:
            await registry.stop()


class TestScanWithParseError:
    async def test_scan_with_parse_error(self, tmp_path: Path) -> None:
        """One valid and one invalid .flow file. Invalid has status=error."""
        (tmp_path / "good.flow").write_text(VALID_FLOW)
        (tmp_path / "bad.flow").write_text(INVALID_FLOW)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flows = registry.list_flows()
            assert len(flows) == 2

            good = registry.get_flow("good")
            assert good is not None
            assert good.status == "valid"

            bad = registry.get_flow("bad")
            assert bad is not None
            assert bad.status == "error"
            assert len(bad.errors) > 0
        finally:
            await registry.stop()


class TestScanWithTypeErrors:
    async def test_scan_with_type_errors(self, tmp_path: Path) -> None:
        """A flow that parses but has type errors is reported as error."""
        (tmp_path / "broken.flow").write_text(FLOW_WITH_TYPE_ERRORS)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flow = registry.get_flow("broken")
            assert flow is not None
            assert flow.status == "error"
            assert len(flow.errors) > 0
            # The flow name should still be extracted from the parse
            assert flow.name == "broken"
        finally:
            await registry.stop()


class TestGetFlowReturnsSource:
    async def test_get_flow_returns_source(self, tmp_path: Path) -> None:
        """get_flow includes source_dsl with the file contents."""
        (tmp_path / "my_flow.flow").write_text(VALID_FLOW)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flow = registry.get_flow("my_flow")
            assert flow is not None
            assert flow.source_dsl == VALID_FLOW
        finally:
            await registry.stop()


class TestGetFlowNotFound:
    async def test_get_flow_not_found(self, tmp_path: Path) -> None:
        """get_flow returns None for a nonexistent flow ID."""
        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            assert registry.get_flow("nonexistent") is None
        finally:
            await registry.stop()


class TestGetFlowAstJson:
    async def test_valid_flow_has_ast_json(self, tmp_path: Path) -> None:
        """A valid flow has ast_json populated with serialized AST."""
        (tmp_path / "my_flow.flow").write_text(VALID_FLOW)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flow = registry.get_flow("my_flow")
            assert flow is not None
            assert flow.ast_json is not None
            assert flow.ast_json["name"] == "test_flow"
            assert "nodes" in flow.ast_json
            assert "edges" in flow.ast_json
        finally:
            await registry.stop()

    async def test_invalid_flow_has_no_ast_json(self, tmp_path: Path) -> None:
        """An invalid flow has ast_json=None."""
        (tmp_path / "bad.flow").write_text(INVALID_FLOW)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flow = registry.get_flow("bad")
            assert flow is not None
            assert flow.ast_json is None
        finally:
            await registry.stop()


class TestGetFlowParams:
    async def test_flow_with_params(self, tmp_path: Path) -> None:
        """A flow with param declarations includes params in the response."""
        (tmp_path / "parameterized.flow").write_text(VALID_FLOW_WITH_PARAMS)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flow = registry.get_flow("parameterized")
            assert flow is not None
            assert flow.status == "valid"
            assert len(flow.params) == 2
            param_names = [p["name"] for p in flow.params]
            assert "focus" in param_names
            assert "verbose" in param_names
            # Check types and defaults
            focus = next(p for p in flow.params if p["name"] == "focus")
            assert focus["type"] == "string"
            assert focus["default_value"] == "all"
            verbose = next(p for p in flow.params if p["name"] == "verbose")
            assert verbose["type"] == "bool"
            assert verbose["default_value"] is False
        finally:
            await registry.stop()


class TestFileWatcherDetectsNewFile:
    async def test_file_watcher_detects_new_file(self, tmp_path: Path) -> None:
        """Start the registry, then write a new .flow file. Verify it appears."""
        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            assert len(registry.list_flows()) == 0

            (tmp_path / "new_flow.flow").write_text(VALID_FLOW)
            await _wait_for(lambda: len(registry.list_flows()) == 1)

            flows = registry.list_flows()
            assert len(flows) == 1
            assert flows[0].id == "new_flow"
            assert flows[0].status == "valid"
        finally:
            await registry.stop()


class TestFileWatcherDetectsModification:
    async def test_file_watcher_detects_modification(self, tmp_path: Path) -> None:
        """Start with a valid file, modify it to introduce an error."""
        (tmp_path / "evolving.flow").write_text(VALID_FLOW)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flow = registry.get_flow("evolving")
            assert flow is not None
            assert flow.status == "valid"

            # Overwrite with invalid content
            (tmp_path / "evolving.flow").write_text(INVALID_FLOW)

            def _is_error() -> bool:
                f = registry.get_flow("evolving")
                return f is not None and f.status == "error"

            await _wait_for(_is_error)

            flow = registry.get_flow("evolving")
            assert flow is not None
            assert flow.status == "error"
            assert len(flow.errors) > 0
        finally:
            await registry.stop()


class TestFileWatcherDetectsDeletion:
    async def test_file_watcher_detects_deletion(self, tmp_path: Path) -> None:
        """Start with a file, delete it. Verify it is removed."""
        flow_path = tmp_path / "to_delete.flow"
        flow_path.write_text(VALID_FLOW)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            assert registry.get_flow("to_delete") is not None

            flow_path.unlink()
            await _wait_for(lambda: registry.get_flow("to_delete") is None)

            assert registry.get_flow("to_delete") is None
            assert len(registry.list_flows()) == 0
        finally:
            await registry.stop()


class TestEmptyWatchDir:
    async def test_empty_watch_dir(self, tmp_path: Path) -> None:
        """FlowRegistry with an empty directory returns empty list."""
        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            assert registry.list_flows() == []
        finally:
            await registry.stop()


class TestWatchDirCreatedIfMissing:
    async def test_watch_dir_created_if_missing(self, tmp_path: Path) -> None:
        """Pass a nonexistent path as watch_dir, verify it gets created on start()."""
        nonexistent = tmp_path / "deeply" / "nested" / "flows"
        assert not nonexistent.exists()

        registry = FlowRegistry(watch_dir=str(nonexistent))
        await registry.start()
        try:
            assert nonexistent.exists()
            assert registry.list_flows() == []
        finally:
            await registry.stop()


class TestNonFlowFilesIgnored:
    async def test_non_flow_files_ignored(self, tmp_path: Path) -> None:
        """Files without .flow extension are ignored during scan."""
        (tmp_path / "readme.txt").write_text("not a flow")
        (tmp_path / "config.toml").write_text("[server]")
        (tmp_path / "good.flow").write_text(VALID_FLOW)

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flows = registry.list_flows()
            assert len(flows) == 1
            assert flows[0].id == "good"
        finally:
            await registry.stop()


class TestBinaryFlowFileHandled:
    async def test_binary_flow_file_handled(self, tmp_path: Path) -> None:
        """A binary file with .flow extension is reported as error."""
        (tmp_path / "binary.flow").write_bytes(b"\x00\x01\x02\xff\xfe")

        registry = FlowRegistry(watch_dir=str(tmp_path))
        await registry.start()
        try:
            flow = registry.get_flow("binary")
            assert flow is not None
            assert flow.status == "error"
            assert len(flow.errors) > 0
        finally:
            await registry.stop()


class TestEventCallback:
    async def test_event_callback_called_on_change(self, tmp_path: Path) -> None:
        """Event callback is called when a file changes."""
        callback = MagicMock()

        registry = FlowRegistry(watch_dir=str(tmp_path))
        registry.set_event_callback(callback)
        await registry.start()
        try:
            (tmp_path / "new.flow").write_text(VALID_FLOW)
            await _wait_for(lambda: callback.call_count >= 1)

            event_type, discovered = callback.call_args[0]
            assert event_type == "file_valid"
            assert isinstance(discovered, DiscoveredFlow)
            assert discovered.id == "new"
        finally:
            await registry.stop()


# ---------------------------------------------------------------------------
# REST endpoint tests (TestClient with mocked FlowRegistry)
# ---------------------------------------------------------------------------


def _make_test_app(
    flows: dict[str, DiscoveredFlow] | None = None,
    db_mock: MagicMock | None = None,
) -> TestClient:
    """Create a TestClient with a mocked FlowRegistry on app.state."""
    config = FlowstateConfig(watch_dir="/tmp/nonexistent-for-test")
    app = create_app(config=config)

    mock_registry = MagicMock(spec=FlowRegistry)
    if flows is None:
        flows = {}
    mock_registry.list_flows.return_value = list(flows.values())
    mock_registry.get_flow.side_effect = lambda fid: flows.get(fid)
    app.state.flow_registry = mock_registry

    # Mock DB — flow endpoints now call db.is_flow_enabled
    if db_mock is None:
        db_mock = MagicMock()
        db_mock.is_flow_enabled.return_value = True
    app.state.db = db_mock

    return TestClient(app, raise_server_exceptions=False)


SAMPLE_FLOW = DiscoveredFlow(
    id="code_review",
    name="code_review",
    file_path="/flows/code_review.flow",
    source_dsl='flow code_review { budget = 10m\non_error = pause\ncontext = handoff\nworkspace = "."\n  entry start { prompt = "go" }\n  exit done { prompt = "done" }\n  start -> done\n}',
    status="valid",
    errors=[],
    ast_json={"name": "code_review", "nodes": {}, "edges": []},
    params=[{"name": "focus", "type": "string", "default_value": "all"}],
)

SAMPLE_FLOW_ERROR = DiscoveredFlow(
    id="broken",
    name=None,
    file_path="/flows/broken.flow",
    source_dsl="invalid dsl",
    status="error",
    errors=["Parse error: unexpected input"],
    ast_json=None,
    params=[],
)


class TestRestListFlows:
    def test_rest_list_flows(self) -> None:
        """GET /api/flows returns 200 with flow list."""
        client = _make_test_app({"code_review": SAMPLE_FLOW, "broken": SAMPLE_FLOW_ERROR})
        response = client.get("/api/flows")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        ids = {f["id"] for f in body}
        assert ids == {"code_review", "broken"}

        # Verify the structure of a valid flow in the list
        valid = next(f for f in body if f["id"] == "code_review")
        assert valid["name"] == "code_review"
        assert valid["status"] == "valid"
        assert valid["errors"] == []
        assert valid["params"] == [{"name": "focus", "type": "string", "default_value": "all"}]
        # Enabled field should be present
        assert valid["enabled"] is True
        # List endpoint should NOT include source_dsl or ast_json
        assert "source_dsl" not in valid
        assert "ast_json" not in valid

    def test_rest_list_flows_empty(self) -> None:
        """GET /api/flows with no flows returns empty list."""
        client = _make_test_app()
        response = client.get("/api/flows")
        assert response.status_code == 200
        assert response.json() == []


class TestRestGetFlow:
    def test_rest_get_flow(self) -> None:
        """GET /api/flows/code_review returns 200 with full flow details."""
        client = _make_test_app({"code_review": SAMPLE_FLOW})
        response = client.get("/api/flows/code_review")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "code_review"
        assert body["name"] == "code_review"
        assert body["file_path"] == "/flows/code_review.flow"
        assert body["status"] == "valid"
        assert body["errors"] == []
        assert body["source_dsl"] == SAMPLE_FLOW.source_dsl
        assert body["ast_json"] == SAMPLE_FLOW.ast_json
        assert body["params"] == SAMPLE_FLOW.params
        assert body["enabled"] is True


class TestRestGetFlowNotFound:
    def test_rest_get_flow_not_found(self) -> None:
        """GET /api/flows/nonexistent returns 404 with error format."""
        client = _make_test_app()
        response = client.get("/api/flows/nonexistent")
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert "nonexistent" in body["error"]
        assert "details" in body


class TestRestGetFlowWithErrors:
    def test_rest_get_flow_with_errors(self) -> None:
        """GET /api/flows/broken returns the error state flow."""
        client = _make_test_app({"broken": SAMPLE_FLOW_ERROR})
        response = client.get("/api/flows/broken")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "broken"
        assert body["status"] == "error"
        assert len(body["errors"]) > 0
        assert body["ast_json"] is None
        assert body["source_dsl"] == "invalid dsl"


# ---------------------------------------------------------------------------
# Flow enable/disable endpoint tests
# ---------------------------------------------------------------------------


class TestEnableFlow:
    def test_enable_flow_returns_200(self) -> None:
        """POST /api/flows/{name}/enable returns 200 with status enabled."""
        mock_db = MagicMock()
        mock_db.is_flow_enabled.return_value = True
        client = _make_test_app(db_mock=mock_db)
        response = client.post("/api/flows/my_flow/enable")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "enabled"
        assert body["flow_name"] == "my_flow"
        mock_db.set_flow_enabled.assert_called_once_with("my_flow", enabled=True)


class TestDisableFlow:
    def test_disable_flow_returns_200(self) -> None:
        """POST /api/flows/{name}/disable returns 200 with status disabled."""
        mock_db = MagicMock()
        mock_db.is_flow_enabled.return_value = True
        client = _make_test_app(db_mock=mock_db)
        response = client.post("/api/flows/my_flow/disable")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "disabled"
        assert body["flow_name"] == "my_flow"
        mock_db.set_flow_enabled.assert_called_once_with("my_flow", enabled=False)


class TestFlowEnabledFieldInList:
    def test_flow_list_includes_enabled_field(self) -> None:
        """GET /api/flows includes enabled field for each flow."""
        mock_db = MagicMock()
        mock_db.is_flow_enabled.return_value = True
        client = _make_test_app(
            {"code_review": SAMPLE_FLOW},
            db_mock=mock_db,
        )
        response = client.get("/api/flows")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["enabled"] is True
        mock_db.is_flow_enabled.assert_called_once_with("code_review")

    def test_flow_list_disabled_flow(self) -> None:
        """GET /api/flows shows enabled=False for disabled flows."""
        mock_db = MagicMock()
        mock_db.is_flow_enabled.return_value = False
        client = _make_test_app(
            {"code_review": SAMPLE_FLOW},
            db_mock=mock_db,
        )
        response = client.get("/api/flows")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["enabled"] is False


class TestEnableDisableToggle:
    def test_enable_then_disable_toggle(self) -> None:
        """Enable and disable toggle calls the DB with correct values."""
        mock_db = MagicMock()
        mock_db.is_flow_enabled.return_value = True
        client = _make_test_app(db_mock=mock_db)

        # Enable
        response = client.post("/api/flows/my_flow/enable")
        assert response.status_code == 200
        assert response.json()["status"] == "enabled"

        # Disable
        response = client.post("/api/flows/my_flow/disable")
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"

        # Verify both DB calls were made
        calls = mock_db.set_flow_enabled.call_args_list
        assert len(calls) == 2
        assert calls[0].args == ("my_flow",)
        assert calls[0].kwargs == {"enabled": True}
        assert calls[1].args == ("my_flow",)
        assert calls[1].kwargs == {"enabled": False}
