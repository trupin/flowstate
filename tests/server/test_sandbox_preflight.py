"""Tests for SERVER-020: Validate openshell availability at run start.

Verifies that starting a sandboxed flow without ``openshell`` on PATH returns
HTTP 400 with a helpful install message, while non-sandboxed flows and flows
with openshell available proceed normally.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from flowstate.config import FlowstateConfig
from flowstate.dsl.ast import (
    ContextMode,
    Edge,
    EdgeType,
    ErrorPolicy,
    Flow,
    Node,
    NodeType,
)
from flowstate.server.app import create_app
from flowstate.server.flow_registry import DiscoveredFlow, FlowRegistry
from flowstate.server.run_manager import RunManager
from flowstate.state.models import FlowDefinitionRow, FlowRunRow

# ---------------------------------------------------------------------------
# Flow DSL source strings
# ---------------------------------------------------------------------------

_SANDBOXED_FLOW_DSL = (
    "flow sandboxed {\n"
    "    budget = 10m\n"
    "    on_error = pause\n"
    "    context = handoff\n"
    "    sandbox = true\n"
    '    workspace = "."\n'
    "\n"
    "    entry start {\n"
    '        prompt = "go"\n'
    "    }\n"
    "\n"
    "    exit done {\n"
    '        prompt = "done"\n'
    "    }\n"
    "\n"
    "    start -> done\n"
    "}\n"
)

_PLAIN_FLOW_DSL = (
    "flow plain {\n"
    "    budget = 10m\n"
    "    on_error = pause\n"
    "    context = handoff\n"
    '    workspace = "."\n'
    "\n"
    "    entry start {\n"
    '        prompt = "go"\n'
    "    }\n"
    "\n"
    "    exit done {\n"
    '        prompt = "done"\n'
    "    }\n"
    "\n"
    "    start -> done\n"
    "}\n"
)

_NODE_SANDBOXED_FLOW_DSL = (
    "flow node_sandboxed {\n"
    "    budget = 10m\n"
    "    on_error = pause\n"
    "    context = handoff\n"
    '    workspace = "."\n'
    "\n"
    "    entry start {\n"
    '        prompt = "go"\n'
    "        sandbox = true\n"
    "    }\n"
    "\n"
    "    exit done {\n"
    '        prompt = "done"\n'
    "    }\n"
    "\n"
    "    start -> done\n"
    "}\n"
)

# ---------------------------------------------------------------------------
# AST objects for mocking parse_flow returns
# ---------------------------------------------------------------------------

_SANDBOXED_FLOW_AST = Flow(
    name="sandboxed",
    budget_seconds=600,
    on_error=ErrorPolicy.PAUSE,
    context=ContextMode.HANDOFF,
    workspace=".",
    sandbox=True,
    nodes={
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="go"),
        "done": Node(name="done", node_type=NodeType.EXIT, prompt="done"),
    },
    edges=(Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="done"),),
)

_PLAIN_FLOW_AST = Flow(
    name="plain",
    budget_seconds=600,
    on_error=ErrorPolicy.PAUSE,
    context=ContextMode.HANDOFF,
    workspace=".",
    sandbox=False,
    nodes={
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="go"),
        "done": Node(name="done", node_type=NodeType.EXIT, prompt="done"),
    },
    edges=(Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="done"),),
)

_NODE_SANDBOXED_FLOW_AST = Flow(
    name="node_sandboxed",
    budget_seconds=600,
    on_error=ErrorPolicy.PAUSE,
    context=ContextMode.HANDOFF,
    workspace=".",
    sandbox=False,
    nodes={
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="go", sandbox=True),
        "done": Node(name="done", node_type=NodeType.EXIT, prompt="done"),
    },
    edges=(Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="done"),),
)

# ---------------------------------------------------------------------------
# Discovered flow objects for the FlowRegistry mock
# ---------------------------------------------------------------------------

SANDBOXED_DISCOVERED = DiscoveredFlow(
    id="sandboxed",
    name="sandboxed",
    file_path="/flows/sandboxed.flow",
    source_dsl=_SANDBOXED_FLOW_DSL,
    status="valid",
    errors=[],
    ast_json={"name": "sandboxed", "nodes": {}, "edges": [], "sandbox": True},
    params=[],
)

PLAIN_DISCOVERED = DiscoveredFlow(
    id="plain",
    name="plain",
    file_path="/flows/plain.flow",
    source_dsl=_PLAIN_FLOW_DSL,
    status="valid",
    errors=[],
    ast_json={"name": "plain", "nodes": {}, "edges": []},
    params=[],
)

NODE_SANDBOXED_DISCOVERED = DiscoveredFlow(
    id="node_sandboxed",
    name="node_sandboxed",
    file_path="/flows/node_sandboxed.flow",
    source_dsl=_NODE_SANDBOXED_FLOW_DSL,
    status="valid",
    errors=[],
    ast_json={"name": "node_sandboxed", "nodes": {}, "edges": []},
    params=[],
)

# ---------------------------------------------------------------------------
# DB helper rows
# ---------------------------------------------------------------------------

SANDBOXED_FLOW_DEF = FlowDefinitionRow(
    id="def-sandboxed",
    name="sandboxed",
    source_dsl=_SANDBOXED_FLOW_DSL,
    ast_json="{}",
    created_at="2025-01-01T00:00:00+00:00",
    updated_at="2025-01-01T00:00:00+00:00",
)

PLAIN_FLOW_DEF = FlowDefinitionRow(
    id="def-plain",
    name="plain",
    source_dsl=_PLAIN_FLOW_DSL,
    ast_json="{}",
    created_at="2025-01-01T00:00:00+00:00",
    updated_at="2025-01-01T00:00:00+00:00",
)


def _make_flow_run_row(
    run_id: str = "run-1",
    flow_def_id: str = "def-sandboxed",
    status: str = "cancelled",
) -> FlowRunRow:
    return FlowRunRow(
        id=run_id,
        flow_definition_id=flow_def_id,
        status=status,
        default_workspace=".",
        data_dir="/data/run-1",
        params_json=None,
        budget_seconds=600,
        elapsed_seconds=10.0,
        on_error="pause",
        started_at="2025-01-01T00:00:00+00:00",
        completed_at=None,
        created_at="2025-01-01T00:00:00+00:00",
        error_message=None,
    )


# ---------------------------------------------------------------------------
# Test client factory
# ---------------------------------------------------------------------------


def _make_test_client(
    flows: dict[str, DiscoveredFlow] | None = None,
    db_mock: MagicMock | None = None,
    run_manager: RunManager | None = None,
) -> TestClient:
    """Create a TestClient with mocked dependencies."""
    config = FlowstateConfig(watch_dir="/tmp/nonexistent-for-test")
    app = create_app(config=config)

    mock_registry = MagicMock(spec=FlowRegistry)
    if flows is None:
        flows = {}
    mock_registry.list_flows.return_value = list(flows.values())
    mock_registry.get_flow.side_effect = lambda fid: flows.get(fid)
    mock_registry.get_flow_by_name.side_effect = lambda name: next(
        (f for f in flows.values() if f.name == name), None
    )
    app.state.flow_registry = mock_registry

    if db_mock is None:
        db_mock = MagicMock()
    app.state.db = db_mock

    if run_manager is None:
        run_manager = RunManager()
    app.state.run_manager = run_manager

    mock_ws_hub = MagicMock()
    mock_ws_hub.on_flow_event = MagicMock()
    app.state.ws_hub = mock_ws_hub

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# TEST-17: Sandboxed flow without openshell returns 400
# ---------------------------------------------------------------------------


class TestSandboxedFlowWithoutOpenshellReturns400:
    def test_start_run_sandboxed_no_openshell(self) -> None:
        """POST /api/flows/:id/runs with sandbox=true and no openshell returns 400."""
        client = _make_test_client(flows={"sandboxed": SANDBOXED_DISCOVERED})

        with (
            patch("flowstate.server.routes.parse_flow", return_value=_SANDBOXED_FLOW_AST),
            patch("flowstate.server.routes.shutil") as mock_shutil,
        ):
            mock_shutil.which.return_value = None

            response = client.post("/api/flows/sandboxed/runs", json={"params": {}})

        assert response.status_code == 400
        body = response.json()
        assert "openshell" in body["error"].lower()
        assert "install" in body["error"].lower()


# ---------------------------------------------------------------------------
# TEST-18: Sandboxed flow with openshell proceeds normally
# ---------------------------------------------------------------------------


def _mock_gateway_ok() -> AsyncMock:
    """Return a mock for asyncio.create_subprocess_exec that simulates a reachable gateway."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    return AsyncMock(return_value=mock_proc)


def _mock_gateway_unreachable(stderr: str = "connection refused") -> AsyncMock:
    """Return a mock for asyncio.create_subprocess_exec that simulates an unreachable gateway."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", stderr.encode()))
    mock_proc.returncode = 1
    return AsyncMock(return_value=mock_proc)


class TestSandboxedFlowWithOpenshellProceeds:
    def test_start_run_sandboxed_with_openshell(self) -> None:
        """POST /api/flows/:id/runs with sandbox=true and openshell installed returns 202."""
        mock_db = MagicMock()
        run_manager = RunManager()
        client = _make_test_client(
            flows={"sandboxed": SANDBOXED_DISCOVERED},
            db_mock=mock_db,
            run_manager=run_manager,
        )

        with (
            patch("flowstate.server.routes.parse_flow", return_value=_SANDBOXED_FLOW_AST),
            patch("flowstate.server.routes.shutil") as mock_shutil,
            patch("flowstate.server.routes.asyncio.create_subprocess_exec", _mock_gateway_ok()),
            patch("flowstate.server.routes.FlowExecutor") as mock_executor_cls,
        ):
            mock_shutil.which.return_value = "/usr/local/bin/openshell"

            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value="run-123")
            mock_executor_cls.return_value = mock_executor

            response = client.post("/api/flows/sandboxed/runs", json={"params": {}})

        assert response.status_code == 202
        body = response.json()
        assert "flow_run_id" in body


# ---------------------------------------------------------------------------
# TEST-19: Non-sandboxed flow skips openshell check
# ---------------------------------------------------------------------------


class TestNonSandboxedFlowSkipsCheck:
    def test_start_run_plain_flow_no_check(self) -> None:
        """POST /api/flows/:id/runs on a non-sandboxed flow does not check openshell."""
        mock_db = MagicMock()
        run_manager = RunManager()
        client = _make_test_client(
            flows={"plain": PLAIN_DISCOVERED},
            db_mock=mock_db,
            run_manager=run_manager,
        )

        with (
            patch("flowstate.server.routes.parse_flow", return_value=_PLAIN_FLOW_AST),
            patch("flowstate.server.routes.shutil") as mock_shutil,
            patch("flowstate.server.routes.FlowExecutor") as mock_executor_cls,
        ):
            mock_shutil.which.return_value = None  # openshell not installed

            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value="run-123")
            mock_executor_cls.return_value = mock_executor

            response = client.post("/api/flows/plain/runs", json={"params": {}})

        # Should succeed even without openshell because sandbox is false
        assert response.status_code == 202
        # shutil.which should NOT have been called (sandbox is false, so the
        # check short-circuits before the which() call)
        mock_shutil.which.assert_not_called()


# ---------------------------------------------------------------------------
# TEST-20: Node-level sandbox=true triggers pre-flight check
# ---------------------------------------------------------------------------


class TestNodeLevelSandboxTriggersCheck:
    def test_start_run_node_sandboxed_no_openshell(self) -> None:
        """Flow with sandbox=false but a node with sandbox=true returns 400 without openshell."""
        client = _make_test_client(flows={"node_sandboxed": NODE_SANDBOXED_DISCOVERED})

        with (
            patch(
                "flowstate.server.routes.parse_flow",
                return_value=_NODE_SANDBOXED_FLOW_AST,
            ),
            patch("flowstate.server.routes.shutil") as mock_shutil,
        ):
            mock_shutil.which.return_value = None

            response = client.post("/api/flows/node_sandboxed/runs", json={"params": {}})

        assert response.status_code == 400
        body = response.json()
        assert "openshell" in body["error"].lower()


# ---------------------------------------------------------------------------
# TEST-21: Error message includes install instructions
# ---------------------------------------------------------------------------


class TestErrorMessageIncludesInstallInstructions:
    def test_error_body_contains_install_url(self) -> None:
        """The 400 error body contains a URL or command for installing openshell."""
        client = _make_test_client(flows={"sandboxed": SANDBOXED_DISCOVERED})

        with (
            patch("flowstate.server.routes.parse_flow", return_value=_SANDBOXED_FLOW_AST),
            patch("flowstate.server.routes.shutil") as mock_shutil,
        ):
            mock_shutil.which.return_value = None

            response = client.post("/api/flows/sandboxed/runs", json={"params": {}})

        assert response.status_code == 400
        body = response.json()
        # Must contain install instructions (URL)
        assert "https://" in body["error"]
        assert "install" in body["error"].lower()


# ---------------------------------------------------------------------------
# TEST-22: Pre-flight check also applies to restart/retry paths
# ---------------------------------------------------------------------------


class TestRestartRetryPathsAlsoCheck:
    def test_retry_terminal_sandboxed_no_openshell(self) -> None:
        """Retry on a terminal sandboxed flow without openshell returns 400."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_flow_definition.return_value = SANDBOXED_FLOW_DEF

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with (
            patch(
                "flowstate.server.routes.parse_flow",
                return_value=_SANDBOXED_FLOW_AST,
            ),
            patch("flowstate.server.routes.shutil") as mock_shutil,
        ):
            mock_shutil.which.return_value = None

            response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 400
        body = response.json()
        assert "openshell" in body["error"].lower()

    def test_skip_terminal_sandboxed_no_openshell(self) -> None:
        """Skip on a terminal sandboxed flow without openshell returns 400."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="failed")
        mock_db.get_flow_definition.return_value = SANDBOXED_FLOW_DEF

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with (
            patch(
                "flowstate.server.routes.parse_flow",
                return_value=_SANDBOXED_FLOW_AST,
            ),
            patch("flowstate.server.routes.shutil") as mock_shutil,
        ):
            mock_shutil.which.return_value = None

            response = client.post("/api/runs/run-1/tasks/task-1/skip")

        assert response.status_code == 400
        body = response.json()
        assert "openshell" in body["error"].lower()

    def test_retry_terminal_plain_flow_proceeds(self) -> None:
        """Retry on a terminal non-sandboxed flow proceeds normally."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(
            flow_def_id="def-plain", status="cancelled"
        )
        mock_db.get_flow_definition.return_value = PLAIN_FLOW_DEF

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with (
            patch(
                "flowstate.server.routes.parse_flow",
                return_value=_PLAIN_FLOW_AST,
            ),
            patch("flowstate.server.routes.shutil") as mock_shutil,
            patch("flowstate.server.routes._create_restart_executor") as mock_create,
        ):
            mock_shutil.which.return_value = None  # openshell not installed

            mock_executor = MagicMock()
            mock_executor.restart_from_task = AsyncMock(return_value="run-1")
            mock_create.return_value = mock_executor

            response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 200
        assert response.json() == {"status": "running"}
        # shutil.which should NOT have been called (sandbox is false)
        mock_shutil.which.assert_not_called()


# ---------------------------------------------------------------------------
# TEST: Trigger schedule with sandbox check
# ---------------------------------------------------------------------------


class TestTriggerScheduleSandboxCheck:
    def test_trigger_sandboxed_schedule_no_openshell(self) -> None:
        """Triggering a sandboxed scheduled flow without openshell returns 400."""
        mock_db = MagicMock()
        mock_schedule = MagicMock()
        mock_schedule.flow_definition_id = "def-sandboxed"
        mock_schedule.enabled = True
        mock_schedule.on_overlap = "allow"
        mock_db.get_flow_schedule.return_value = mock_schedule
        mock_db.get_flow_definition.return_value = SANDBOXED_FLOW_DEF

        client = _make_test_client(db_mock=mock_db)

        with (
            patch(
                "flowstate.server.routes.parse_flow",
                return_value=_SANDBOXED_FLOW_AST,
            ),
            patch("flowstate.server.routes.shutil") as mock_shutil,
        ):
            mock_shutil.which.return_value = None

            response = client.post("/api/schedules/sched-1/trigger")

        assert response.status_code == 400
        body = response.json()
        assert "openshell" in body["error"].lower()


# ---------------------------------------------------------------------------
# Gateway reachability tests
# ---------------------------------------------------------------------------


class TestGatewayReachabilityCheck:
    def test_gateway_unreachable_returns_400(self) -> None:
        """Sandboxed flow with openshell installed but gateway unreachable returns 400."""
        client = _make_test_client(flows={"sandboxed": SANDBOXED_DISCOVERED})

        with (
            patch("flowstate.server.routes.parse_flow", return_value=_SANDBOXED_FLOW_AST),
            patch("flowstate.server.routes.shutil") as mock_shutil,
            patch(
                "flowstate.server.routes.asyncio.create_subprocess_exec",
                _mock_gateway_unreachable("connection refused"),
            ),
        ):
            mock_shutil.which.return_value = "/usr/local/bin/openshell"

            response = client.post("/api/flows/sandboxed/runs", json={"params": {}})

        assert response.status_code == 400
        body = response.json()
        assert "gateway" in body["error"].lower()
        assert "openshell gateway start" in body["error"]

    def test_gateway_unreachable_includes_stderr(self) -> None:
        """Error message includes stderr from the openshell command."""
        client = _make_test_client(flows={"sandboxed": SANDBOXED_DISCOVERED})

        with (
            patch("flowstate.server.routes.parse_flow", return_value=_SANDBOXED_FLOW_AST),
            patch("flowstate.server.routes.shutil") as mock_shutil,
            patch(
                "flowstate.server.routes.asyncio.create_subprocess_exec",
                _mock_gateway_unreachable("dial tcp 127.0.0.1:7800: connection refused"),
            ),
        ):
            mock_shutil.which.return_value = "/usr/local/bin/openshell"

            response = client.post("/api/flows/sandboxed/runs", json={"params": {}})

        assert response.status_code == 400
        body = response.json()
        assert "connection refused" in body["error"]

    def test_gateway_timeout_returns_400(self) -> None:
        """Sandboxed flow where gateway check times out returns 400."""
        client = _make_test_client(flows={"sandboxed": SANDBOXED_DISCOVERED})

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError("timed out"))
        mock_subprocess = AsyncMock(return_value=mock_proc)

        with (
            patch("flowstate.server.routes.parse_flow", return_value=_SANDBOXED_FLOW_AST),
            patch("flowstate.server.routes.shutil") as mock_shutil,
            patch(
                "flowstate.server.routes.asyncio.create_subprocess_exec",
                mock_subprocess,
            ),
            patch("flowstate.server.routes.asyncio.wait_for", side_effect=TimeoutError),
        ):
            mock_shutil.which.return_value = "/usr/local/bin/openshell"

            response = client.post("/api/flows/sandboxed/runs", json={"params": {}})

        assert response.status_code == 400
        body = response.json()
        assert "timed out" in body["error"].lower()

    def test_gateway_os_error_returns_400(self) -> None:
        """Sandboxed flow where openshell subprocess raises OSError returns 400."""
        client = _make_test_client(flows={"sandboxed": SANDBOXED_DISCOVERED})

        mock_subprocess = AsyncMock(side_effect=OSError("No such file"))

        with (
            patch("flowstate.server.routes.parse_flow", return_value=_SANDBOXED_FLOW_AST),
            patch("flowstate.server.routes.shutil") as mock_shutil,
            patch(
                "flowstate.server.routes.asyncio.create_subprocess_exec",
                mock_subprocess,
            ),
        ):
            mock_shutil.which.return_value = "/usr/local/bin/openshell"

            response = client.post("/api/flows/sandboxed/runs", json={"params": {}})

        assert response.status_code == 400
        body = response.json()
        assert "openshell" in body["error"].lower()

    def test_non_sandboxed_flow_skips_gateway_check(self) -> None:
        """Non-sandboxed flow does not check gateway reachability."""
        mock_db = MagicMock()
        run_manager = RunManager()
        client = _make_test_client(
            flows={"plain": PLAIN_DISCOVERED},
            db_mock=mock_db,
            run_manager=run_manager,
        )

        with (
            patch("flowstate.server.routes.parse_flow", return_value=_PLAIN_FLOW_AST),
            patch("flowstate.server.routes.shutil") as mock_shutil,
            patch(
                "flowstate.server.routes.asyncio.create_subprocess_exec",
            ) as mock_subprocess,
            patch("flowstate.server.routes.FlowExecutor") as mock_executor_cls,
        ):
            mock_shutil.which.return_value = None

            mock_executor = MagicMock()
            mock_executor.execute = AsyncMock(return_value="run-123")
            mock_executor_cls.return_value = mock_executor

            response = client.post("/api/flows/plain/runs", json={"params": {}})

        assert response.status_code == 202
        mock_subprocess.assert_not_called()

    def test_restart_gateway_unreachable_returns_400(self) -> None:
        """Retry on sandboxed flow with unreachable gateway returns 400."""
        mock_db = MagicMock()
        mock_db.get_flow_run.return_value = _make_flow_run_row(status="cancelled")
        mock_db.get_flow_definition.return_value = SANDBOXED_FLOW_DEF

        run_manager = RunManager()
        client = _make_test_client(db_mock=mock_db, run_manager=run_manager)

        with (
            patch(
                "flowstate.server.routes.parse_flow",
                return_value=_SANDBOXED_FLOW_AST,
            ),
            patch("flowstate.server.routes.shutil") as mock_shutil,
            patch(
                "flowstate.server.routes.asyncio.create_subprocess_exec",
                _mock_gateway_unreachable("gateway not running"),
            ),
        ):
            mock_shutil.which.return_value = "/usr/local/bin/openshell"

            response = client.post("/api/runs/run-1/tasks/task-1/retry")

        assert response.status_code == 400
        body = response.json()
        assert "gateway" in body["error"].lower()
