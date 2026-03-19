"""Tests for orchestrator session manager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from flowstate.dsl.ast import ContextMode, Edge, EdgeType, ErrorPolicy, Flow, Node, NodeType
from flowstate.engine.orchestrator import OrchestratorManager, OrchestratorSession
from flowstate.engine.subprocess_mgr import SubprocessManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_flow(name: str = "test_flow", workspace: str = "/project") -> Flow:
    """Build a minimal Flow for testing."""
    nodes = {
        "start": Node(name="start", node_type=NodeType.ENTRY, prompt="Begin"),
        "work": Node(name="work", node_type=NodeType.TASK, prompt="Do work"),
        "done": Node(name="done", node_type=NodeType.EXIT, prompt="Finish"),
    }
    edges = (
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="work"),
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="work", target="done"),
    )
    return Flow(
        name=name,
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=edges,
    )


async def _empty_stream():
    """An async generator that yields nothing (simulates consuming a stream)."""
    return
    yield  # make this an async generator


def _mock_subprocess_mgr() -> SubprocessManager:
    """Create a SubprocessManager mock with run_task returning an empty async generator.

    run_task is an async generator function, so the mock must return an async iterable
    (not a coroutine). We use a regular Mock with side_effect to produce a fresh
    async generator on each call.
    """
    from unittest.mock import Mock

    mgr = Mock(spec=SubprocessManager)
    mgr.run_task = Mock(side_effect=lambda *args, **kwargs: _empty_stream())
    mgr.kill = AsyncMock()
    return mgr


# ---------------------------------------------------------------------------
# Tests: OrchestratorSession dataclass
# ---------------------------------------------------------------------------


class TestOrchestratorSession:
    def test_default_not_initialized(self) -> None:
        session = OrchestratorSession(
            session_id="abc",
            harness="claude",
            cwd="/project",
            data_dir="/data",
        )
        assert session.is_initialized is False

    def test_initialized_flag(self) -> None:
        session = OrchestratorSession(
            session_id="abc",
            harness="claude",
            cwd="/project",
            data_dir="/data",
            is_initialized=True,
        )
        assert session.is_initialized is True


# ---------------------------------------------------------------------------
# Tests: OrchestratorManager
# ---------------------------------------------------------------------------


class TestGetOrCreateNew:
    @pytest.mark.asyncio
    async def test_creates_session(self, tmp_path: Path) -> None:
        """First call creates a new session with session_id and is_initialized=True."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        session = await manager.get_or_create(
            harness="claude",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        assert session.session_id  # non-empty
        assert session.harness == "claude"
        assert session.cwd == "/project"
        assert session.is_initialized is True

    @pytest.mark.asyncio
    async def test_calls_subprocess_run_task(self, tmp_path: Path) -> None:
        """Creating a session invokes subprocess_mgr.run_task with the system prompt."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        await manager.get_or_create(
            harness="claude",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        mock_mgr.run_task.assert_called_once()
        call_args = mock_mgr.run_task.call_args
        prompt_arg = call_args[0][0]
        assert "Flowstate Orchestrator Agent" in prompt_arg
        assert call_args[0][1] == "/project"  # cwd


class TestGetOrCreateCached:
    @pytest.mark.asyncio
    async def test_returns_same_session(self, tmp_path: Path) -> None:
        """Second call for same (harness, cwd) returns the cached session."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        session1 = await manager.get_or_create(
            harness="claude",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )
        # Reset mock so run_task returns a fresh generator for any new call
        mock_mgr.run_task = AsyncMock(return_value=_empty_stream())

        session2 = await manager.get_or_create(
            harness="claude",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        assert session1 is session2
        # run_task should NOT have been called a second time
        mock_mgr.run_task.assert_not_called()


class TestDifferentCwds:
    @pytest.mark.asyncio
    async def test_different_cwds_create_different_sessions(self, tmp_path: Path) -> None:
        """Two different cwds produce two independent sessions."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        session_a = await manager.get_or_create(
            harness="claude",
            cwd="/project-a",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        session_b = await manager.get_or_create(
            harness="claude",
            cwd="/project-b",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        assert session_a is not session_b
        assert session_a.session_id != session_b.session_id
        assert session_a.cwd == "/project-a"
        assert session_b.cwd == "/project-b"

    @pytest.mark.asyncio
    async def test_different_harnesses_create_different_sessions(self, tmp_path: Path) -> None:
        """Two different harness names for the same cwd produce different sessions."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        session_a = await manager.get_or_create(
            harness="claude-a",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        session_b = await manager.get_or_create(
            harness="claude-b",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        assert session_a is not session_b
        assert session_a.session_id != session_b.session_id


class TestTerminate:
    @pytest.mark.asyncio
    async def test_terminate_removes_session(self, tmp_path: Path) -> None:
        """Terminate removes the session from tracking and calls kill."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        session = await manager.get_or_create(
            harness="claude",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        await manager.terminate(session.session_id)

        mock_mgr.kill.assert_called_once_with(session.session_id)
        assert len(manager._sessions) == 0

    @pytest.mark.asyncio
    async def test_terminate_nonexistent_session(self, tmp_path: Path) -> None:
        """Terminating a non-tracked session_id calls kill but doesn't error."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)

        # Should not raise
        await manager.terminate("nonexistent-id")
        mock_mgr.kill.assert_called_once_with("nonexistent-id")


class TestTerminateAll:
    @pytest.mark.asyncio
    async def test_terminate_all_clears_sessions(self, tmp_path: Path) -> None:
        """terminate_all kills all sessions and clears tracking."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        session_a = await manager.get_or_create(
            harness="claude",
            cwd="/project-a",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        session_b = await manager.get_or_create(
            harness="claude",
            cwd="/project-b",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        await manager.terminate_all("run-001")

        assert len(manager._sessions) == 0
        # kill called for both sessions
        kill_calls = [call[0][0] for call in mock_mgr.kill.call_args_list]
        assert session_a.session_id in kill_calls
        assert session_b.session_id in kill_calls


class TestSessionPersistence:
    @pytest.mark.asyncio
    async def test_session_id_written_to_disk(self, tmp_path: Path) -> None:
        """session_id file written to orchestrator data directory."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        session = await manager.get_or_create(
            harness="claude",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        session_id_path = Path(session.data_dir) / "session_id"
        assert session_id_path.exists()
        assert session_id_path.read_text() == session.session_id

    @pytest.mark.asyncio
    async def test_system_prompt_written_to_disk(self, tmp_path: Path) -> None:
        """system_prompt.md written to orchestrator data directory."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        session = await manager.get_or_create(
            harness="claude",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        prompt_path = Path(session.data_dir) / "system_prompt.md"
        assert prompt_path.exists()
        content = prompt_path.read_text()
        assert "Flowstate Orchestrator Agent" in content
        assert "test_flow" in content

    @pytest.mark.asyncio
    async def test_data_dir_under_run_data_dir(self, tmp_path: Path) -> None:
        """Session data_dir is under <run_data_dir>/orchestrator/<key>/."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        session = await manager.get_or_create(
            harness="claude",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
        )

        data_dir = Path(session.data_dir)
        assert data_dir.parent.name == "orchestrator"
        assert data_dir.parent.parent == tmp_path


class TestSessionKey:
    def test_deterministic(self) -> None:
        """Same (harness, cwd) always produces the same key."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)

        key1 = manager._session_key("claude", "/project")
        key2 = manager._session_key("claude", "/project")
        assert key1 == key2

    def test_different_cwds_produce_different_keys(self) -> None:
        """Different cwds produce different keys."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)

        key_a = manager._session_key("claude", "/project-a")
        key_b = manager._session_key("claude", "/project-b")
        assert key_a != key_b

    def test_different_harnesses_produce_different_keys(self) -> None:
        """Different harness names produce different keys."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)

        key_a = manager._session_key("claude-a", "/project")
        key_b = manager._session_key("claude-b", "/project")
        assert key_a != key_b

    def test_key_format(self) -> None:
        """Key is formatted as <harness>-<cwd_hash_prefix>."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)

        key = manager._session_key("claude", "/project")
        assert key.startswith("claude-")
        # Hash portion is 12 hex chars
        hash_part = key[len("claude-") :]
        assert len(hash_part) == 12
        int(hash_part, 16)  # should be valid hex


class TestSkipPermissions:
    @pytest.mark.asyncio
    async def test_skip_permissions_forwarded(self, tmp_path: Path) -> None:
        """skip_permissions flag is passed through to subprocess_mgr.run_task."""
        mock_mgr = _mock_subprocess_mgr()
        manager = OrchestratorManager(mock_mgr)
        flow = _make_flow()

        await manager.get_or_create(
            harness="claude",
            cwd="/project",
            flow=flow,
            run_id="run-001",
            run_data_dir=str(tmp_path),
            skip_permissions=True,
        )

        mock_mgr.run_task.assert_called_once()
        call_kwargs = mock_mgr.run_task.call_args[1]
        assert call_kwargs["skip_permissions"] is True
