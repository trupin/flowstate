"""Tests for the SandboxManager (OpenShell lifecycle)."""

import asyncio
from pathlib import Path
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
    """wrap_command() uses 'openshell sandbox create' with --no-keep (ENGINE-064)."""

    def test_basic_create_format_no_credentials(self) -> None:
        """wrap_command uses 'create' with provisioning flags and -- command (no credentials)."""
        mgr = SandboxManager()
        dockerfile_path = mgr._dockerfile_path()
        with patch.object(mgr, "_claude_credentials_path", return_value=None):
            result = mgr.wrap_command(["claude-agent-acp"], "abc123def456")
        assert result == [
            "openshell",
            "sandbox",
            "create",
            "--name",
            "fs-abc123def456",
            "--from",
            dockerfile_path,
            "--auto-providers",
            "--no-tty",
            "--no-keep",
            "--",
            "claude-agent-acp",
        ]

    def test_basic_create_format_with_credentials(self) -> None:
        """wrap_command includes --upload when credentials exist."""
        mgr = SandboxManager()
        dockerfile_path = mgr._dockerfile_path()
        creds_path = "/home/user/.claude.json"
        with patch.object(mgr, "_claude_credentials_path", return_value=creds_path):
            result = mgr.wrap_command(["claude-agent-acp"], "abc123def456")
        assert result == [
            "openshell",
            "sandbox",
            "create",
            "--name",
            "fs-abc123def456",
            "--from",
            dockerfile_path,
            "--auto-providers",
            "--no-tty",
            "--no-keep",
            "--upload",
            f"{creds_path}:/sandbox/.claude.json",
            "--",
            "claude-agent-acp",
        ]

    def test_preserves_multi_args(self) -> None:
        """Multi-argument commands are preserved after --."""
        mgr = SandboxManager()
        result = mgr.wrap_command(
            ["claude", "--model", "opus", "--verbose"],
            "abc123def456",
        )
        # Everything after -- should be the original command
        separator_idx = result.index("--")
        assert result[separator_idx + 1 :] == ["claude", "--model", "opus", "--verbose"]

    def test_includes_provisioning_flags(self) -> None:
        """create command includes --from, --auto-providers, --no-tty, --no-keep."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude"], "abc123def456")
        assert "--from" in result
        assert "--auto-providers" in result
        assert "--no-tty" in result
        assert "--no-keep" in result

    def test_from_uses_dockerfile_path(self) -> None:
        """--from argument points to the sandbox Dockerfile directory."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude"], "abc123def456")
        from_idx = result.index("--from")
        assert result[from_idx + 1] == mgr._dockerfile_path()

    def test_sandbox_policy_included_when_provided(self) -> None:
        """sandbox_policy adds --policy flag before the -- separator."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude"], "abc123def456", sandbox_policy="strict.yaml")
        assert "--policy" in result
        policy_idx = result.index("--policy")
        assert result[policy_idx + 1] == "strict.yaml"
        # --policy should be before the -- separator
        separator_idx = result.index("--")
        assert policy_idx < separator_idx

    def test_no_policy_when_none(self) -> None:
        """sandbox_policy=None omits --policy flag."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude"], "abc123def456", sandbox_policy=None)
        assert "--policy" not in result

    def test_uses_sandbox_name(self) -> None:
        """--name uses the deterministic sandbox name."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude"], "abcdef123456789")
        name_idx = result.index("--name")
        assert result[name_idx + 1] == "fs-abcdef123456"


# ---------------------------------------------------------------------------
# _dockerfile_path and _claude_credentials_path (ENGINE-064)
# ---------------------------------------------------------------------------


class TestDockerfilePath:
    """_dockerfile_path() returns the path to the sandbox Dockerfile directory."""

    def test_points_to_sandbox_dir(self) -> None:
        """_dockerfile_path() returns a path ending in 'sandbox' containing a Dockerfile."""
        mgr = SandboxManager()
        path = mgr._dockerfile_path()
        assert path.endswith("sandbox")
        assert (Path(path) / "Dockerfile").is_file()

    def test_path_is_absolute(self) -> None:
        """_dockerfile_path() returns an absolute path."""
        mgr = SandboxManager()
        path = mgr._dockerfile_path()
        assert Path(path).is_absolute()

    def test_path_relative_to_sandbox_module(self) -> None:
        """_dockerfile_path() is relative to the sandbox.py module."""
        mgr = SandboxManager()
        path = mgr._dockerfile_path()
        # Should be a sibling directory of sandbox.py
        import flowstate.engine.sandbox as sandbox_mod

        expected = str(Path(sandbox_mod.__file__).parent / "sandbox")
        assert path == expected


class TestClaudeCredentialsPath:
    """_claude_credentials_path() returns the path to ~/.claude.json if it exists."""

    def test_returns_path_when_exists(self) -> None:
        """Returns the string path when ~/.claude.json exists."""
        mgr = SandboxManager()
        with patch.object(Path, "exists", return_value=True):
            result = mgr._claude_credentials_path()
        assert result is not None
        assert result.endswith(".claude.json")

    def test_returns_none_when_missing(self) -> None:
        """Returns None when ~/.claude.json does not exist."""
        mgr = SandboxManager()
        with patch.object(Path, "exists", return_value=False):
            result = mgr._claude_credentials_path()
        assert result is None

    def test_path_is_in_home_directory(self) -> None:
        """The credentials path is in the user's home directory."""
        mgr = SandboxManager()
        with patch.object(Path, "exists", return_value=True):
            result = mgr._claude_credentials_path()
        assert result is not None
        assert result == str(Path.home() / ".claude.json")


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
