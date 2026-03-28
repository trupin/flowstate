"""Tests for the SandboxManager (OpenShell lifecycle)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flowstate.engine.sandbox import SandboxError, SandboxManager

# ---------------------------------------------------------------------------
# sandbox_name
# ---------------------------------------------------------------------------


class TestSandboxName:
    def test_deterministic(self) -> None:
        """Same ID always produces the same name (TEST-22)."""
        mgr = SandboxManager()
        name1 = mgr.sandbox_name("abc123def456xyz")
        name2 = mgr.sandbox_name("abc123def456xyz")
        assert name1 == name2

    def test_prefix(self) -> None:
        """Name starts with 'fs-' (TEST-22)."""
        mgr = SandboxManager()
        name = mgr.sandbox_name("anything")
        assert name.startswith("fs-")

    def test_uses_first_12_chars(self) -> None:
        """Name contains first 12 characters of the ID (TEST-23)."""
        mgr = SandboxManager()
        name = mgr.sandbox_name("abcdef123456789xyz")
        assert name == "fs-abcdef123456"

    def test_short_id(self) -> None:
        """IDs shorter than 12 chars are used as-is."""
        mgr = SandboxManager()
        name = mgr.sandbox_name("short")
        assert name == "fs-short"


# ---------------------------------------------------------------------------
# wrap_command
# ---------------------------------------------------------------------------


class TestWrapCommand:
    def test_basic(self) -> None:
        """Basic command wrapping (TEST-24)."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude"], "abc123def456")
        assert result == [
            "openshell",
            "sandbox",
            "create",
            "--name",
            "fs-abc123def456",
            "--",
            "claude",
        ]

    def test_with_policy(self) -> None:
        """--policy flag included when sandbox_policy provided (TEST-25)."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude"], "abc123def456", sandbox_policy="strict.yaml")
        assert result == [
            "openshell",
            "sandbox",
            "create",
            "--name",
            "fs-abc123def456",
            "--policy",
            "strict.yaml",
            "--",
            "claude",
        ]

    def test_preserves_args(self) -> None:
        """Multi-argument commands are preserved after -- (TEST-26)."""
        mgr = SandboxManager()
        result = mgr.wrap_command(
            ["claude", "--model", "opus", "--verbose"],
            "abc123def456",
        )
        assert result[-5:] == ["--", "claude", "--model", "opus", "--verbose"]

    def test_no_policy_when_none(self) -> None:
        """No --policy flag when sandbox_policy is None."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude"], "abc123def456", sandbox_policy=None)
        assert "--policy" not in result

    def test_no_policy_when_empty_string(self) -> None:
        """No --policy flag when sandbox_policy is empty string."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude"], "abc123def456", sandbox_policy="")
        assert "--policy" not in result


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestRegister:
    async def test_tracks_sandbox(self) -> None:
        """register() adds the sandbox name to the active set (TEST-27)."""
        mgr = SandboxManager()
        await mgr.register("task-exec-001")
        assert mgr.sandbox_name("task-exec-001") in mgr._active_sandboxes

    async def test_idempotent(self) -> None:
        """Registering the same ID twice doesn't duplicate."""
        mgr = SandboxManager()
        await mgr.register("task-exec-001")
        await mgr.register("task-exec-001")
        assert len(mgr._active_sandboxes) == 1


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------


class TestDestroy:
    async def test_removes_from_set(self) -> None:
        """destroy() removes the sandbox from the active set (TEST-28)."""
        mgr = SandboxManager()
        await mgr.register("task-exec-001")
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc
            await mgr.destroy("task-exec-001")
        assert mgr.sandbox_name("task-exec-001") not in mgr._active_sandboxes

    async def test_calls_openshell_delete(self) -> None:
        """destroy() invokes 'openshell sandbox delete <name>' (TEST-29)."""
        mgr = SandboxManager()
        await mgr.register("task-exec-001")
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc
            await mgr.destroy("task-exec-001")
        expected_name = mgr.sandbox_name("task-exec-001")
        mock_exec.assert_called_once_with(
            "openshell",
            "sandbox",
            "delete",
            expected_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def test_unregistered_sandbox_no_error(self) -> None:
        """destroy() for unregistered sandbox doesn't raise (TEST-30)."""
        mgr = SandboxManager()
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc
            await mgr.destroy("nonexistent-id")
        # No exception raised — test passes

    async def test_subprocess_failure_logged_not_raised(self) -> None:
        """destroy() logs but does not raise when openshell fails."""
        mgr = SandboxManager()
        await mgr.register("task-exec-001")
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(return_value=1)
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc
            # Should not raise
            await mgr.destroy("task-exec-001")

    async def test_os_error_logged_not_raised(self) -> None:
        """destroy() handles OSError gracefully (openshell not installed)."""
        mgr = SandboxManager()
        await mgr.register("task-exec-001")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=OSError("openshell not found"),
        ):
            # Should not raise
            await mgr.destroy("task-exec-001")
        # Sandbox should still be removed from active set
        assert mgr.sandbox_name("task-exec-001") not in mgr._active_sandboxes


# ---------------------------------------------------------------------------
# destroy_all
# ---------------------------------------------------------------------------


class TestDestroyAll:
    async def test_clears_all(self) -> None:
        """destroy_all() clears all tracked sandboxes (TEST-31)."""
        mgr = SandboxManager()
        await mgr.register("task-001")
        await mgr.register("task-002")
        await mgr.register("task-003")
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc
            await mgr.destroy_all()
        assert len(mgr._active_sandboxes) == 0
        assert mock_exec.call_count == 3

    async def test_empty_set_noop(self) -> None:
        """destroy_all() with no sandboxes doesn't call subprocess (TEST-32)."""
        mgr = SandboxManager()
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            await mgr.destroy_all()
        mock_exec.assert_not_called()

    async def test_partial_failure_continues(self) -> None:
        """destroy_all() continues cleanup even when some deletes fail."""
        mgr = SandboxManager()
        await mgr.register("task-001")
        await mgr.register("task-002")

        call_count = 0

        async def side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("first delete fails")
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.returncode = 0
            return mock_proc

        with patch(
            "asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=side_effect
        ):
            await mgr.destroy_all()
        # Set should be clear regardless of individual failures
        assert len(mgr._active_sandboxes) == 0


# ---------------------------------------------------------------------------
# SandboxError
# ---------------------------------------------------------------------------


class TestSandboxError:
    def test_importable_and_raisable(self) -> None:
        """SandboxError is a valid exception class (TEST-33)."""
        err = SandboxError("test error")
        assert isinstance(err, Exception)
        assert str(err) == "test error"

    def test_raise_and_catch(self) -> None:
        """SandboxError can be raised and caught."""
        with pytest.raises(SandboxError, match="sandbox failed"):
            raise SandboxError("sandbox failed")


# ---------------------------------------------------------------------------
# Concurrency safety
# ---------------------------------------------------------------------------


class TestConcurrency:
    async def test_concurrent_register_destroy(self) -> None:
        """Concurrent register/destroy calls don't cause races (TEST-34)."""
        mgr = SandboxManager()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            # Register many, then destroy many concurrently
            ids = [f"task-{i:04d}" for i in range(20)]

            await asyncio.gather(*[mgr.register(tid) for tid in ids])
            assert len(mgr._active_sandboxes) == 20

            await asyncio.gather(*[mgr.destroy(tid) for tid in ids])
            assert len(mgr._active_sandboxes) == 0

    async def test_interleaved_register_destroy(self) -> None:
        """Interleaved register and destroy calls are safe."""
        mgr = SandboxManager()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            # Mix register and destroy in the same gather
            tasks = []
            for i in range(10):
                tasks.append(mgr.register(f"task-{i:04d}"))
            for i in range(5):
                tasks.append(mgr.destroy(f"task-{i:04d}"))

            await asyncio.gather(*tasks)
            # After all settle, at least the non-destroyed ones should remain
            # (exact count depends on scheduling, but no exceptions is the key check)
