"""Tests for the SandboxManager (persistent sandbox connect-wrapper model)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from flowstate.engine.sandbox import SandboxManager

# ---------------------------------------------------------------------------
# Default sandbox name
# ---------------------------------------------------------------------------


class TestDefaultSandboxName:
    def test_default_name(self) -> None:
        """SandboxManager uses 'flowstate-claude' by default."""
        mgr = SandboxManager()
        assert mgr.sandbox_name == "flowstate-claude"

    def test_custom_name(self) -> None:
        """SandboxManager accepts a custom sandbox name."""
        mgr = SandboxManager(sandbox_name="my-sandbox")
        assert mgr.sandbox_name == "my-sandbox"


# ---------------------------------------------------------------------------
# _connect_wrapper_path
# ---------------------------------------------------------------------------


class TestConnectWrapperPath:
    def test_returns_path_ending_with_connect_wrapper_sh(self) -> None:
        """_connect_wrapper_path() returns a path ending in 'connect-wrapper.sh'."""
        mgr = SandboxManager()
        path = mgr._connect_wrapper_path()
        assert path.endswith("connect-wrapper.sh")

    def test_path_is_absolute(self) -> None:
        """_connect_wrapper_path() returns an absolute path."""
        mgr = SandboxManager()
        path = mgr._connect_wrapper_path()
        assert Path(path).is_absolute()

    def test_path_relative_to_sandbox_module(self) -> None:
        """_connect_wrapper_path() is in the sandbox/ subdirectory next to sandbox.py."""
        mgr = SandboxManager()
        path = mgr._connect_wrapper_path()
        import flowstate.engine.sandbox as sandbox_mod

        expected = str(Path(sandbox_mod.__file__).parent / "sandbox" / "connect-wrapper.sh")
        assert path == expected

    def test_connect_wrapper_exists(self) -> None:
        """The sandbox/connect-wrapper.sh file exists on disk."""
        mgr = SandboxManager()
        wrapper = Path(mgr._connect_wrapper_path())
        assert wrapper.is_file()


# ---------------------------------------------------------------------------
# wrap_command
# ---------------------------------------------------------------------------


class TestWrapCommand:
    """wrap_command() uses the connect-wrapper.sh script with sandbox name."""

    def test_returns_three_element_list(self) -> None:
        """wrap_command returns [wrapper_path, sandbox_name, quoted_command]."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude-agent-acp"])
        assert len(result) == 3

    def test_first_element_is_wrapper_path(self) -> None:
        """First element is the connect-wrapper.sh path."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude-agent-acp"])
        assert result[0] == mgr._connect_wrapper_path()
        assert result[0].endswith("connect-wrapper.sh")

    def test_second_element_is_sandbox_name(self) -> None:
        """Second element is the sandbox name."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude-agent-acp"])
        assert result[1] == "flowstate-claude"

    def test_custom_sandbox_name(self) -> None:
        """Custom sandbox name appears as the second element."""
        mgr = SandboxManager(sandbox_name="my-custom-sandbox")
        result = mgr.wrap_command(["claude-agent-acp"])
        assert result[1] == "my-custom-sandbox"

    def test_third_element_is_quoted_command(self) -> None:
        """Third element is the shell-quoted command string."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude-agent-acp"])
        assert result[2] == "claude-agent-acp"

    def test_multi_arg_command_quoted(self) -> None:
        """Multi-argument commands are shell-quoted into a single string."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["claude", "--model", "opus", "--verbose"])
        assert result[2] == "claude --model opus --verbose"

    def test_special_chars_quoted(self) -> None:
        """Special characters in arguments are properly shell-quoted."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["echo", "hello world", "it's"])
        # shlex.quote wraps strings with spaces/special chars
        assert "hello world" in result[2]
        assert "'" in result[2] or "it" in result[2]

    def test_empty_command(self) -> None:
        """Empty command list produces an empty quoted string."""
        mgr = SandboxManager()
        result = mgr.wrap_command([])
        assert result[2] == ""

    def test_full_command_structure(self) -> None:
        """Verify the entire command list structure end-to-end."""
        mgr = SandboxManager()
        result = mgr.wrap_command(["my-agent"])
        expected = [
            mgr._connect_wrapper_path(),
            "flowstate-claude",
            "my-agent",
        ]
        assert result == expected


# ---------------------------------------------------------------------------
# No legacy API
# ---------------------------------------------------------------------------


class TestNoLegacyApi:
    """SandboxManager has no leftover methods from the old per-task sandbox model."""

    def test_no_dockerfile_path(self) -> None:
        """SandboxManager has no _dockerfile_path method."""
        mgr = SandboxManager()
        assert not hasattr(mgr, "_dockerfile_path")

    def test_no_register_destroy(self) -> None:
        """SandboxManager has no register/destroy methods."""
        mgr = SandboxManager()
        assert not hasattr(mgr, "register")
        assert not hasattr(mgr, "destroy")
        assert not hasattr(mgr, "destroy_all")


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------


class TestDownloadFile:
    """download_file() calls openshell sandbox download and returns success/failure."""

    @pytest.mark.asyncio
    async def test_download_success(self) -> None:
        """Returns True when openshell exits with code 0."""
        mgr = SandboxManager(sandbox_name="test-sandbox")
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await mgr.download_file("/sandbox/DECISION.json", "/host/path/DECISION.json")

        assert result is True
        mock_exec.assert_called_once_with(
            "openshell",
            "sandbox",
            "download",
            "test-sandbox",
            "/sandbox/DECISION.json",
            "/host/path/DECISION.json",
            stdout=-1,  # asyncio.subprocess.PIPE
            stderr=-1,
        )
        mock_proc.wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_failure_nonzero_exit(self) -> None:
        """Returns False when openshell exits with non-zero code (file not found)."""
        mgr = SandboxManager(sandbox_name="test-sandbox")
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await mgr.download_file("/sandbox/DECISION.json", "/host/path/DECISION.json")

        assert result is False

    @pytest.mark.asyncio
    async def test_download_oserror(self) -> None:
        """Returns False when openshell binary is not found (OSError)."""
        mgr = SandboxManager(sandbox_name="test-sandbox")

        with patch("asyncio.create_subprocess_exec", side_effect=OSError("not found")):
            result = await mgr.download_file("/sandbox/DECISION.json", "/host/path/DECISION.json")

        assert result is False

    @pytest.mark.asyncio
    async def test_download_uses_sandbox_name(self) -> None:
        """download_file passes the correct sandbox name to openshell."""
        mgr = SandboxManager(sandbox_name="my-custom-sandbox")
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await mgr.download_file("/sandbox/file.txt", "/host/file.txt")

        args = mock_exec.call_args[0]
        assert args[3] == "my-custom-sandbox"

    @pytest.mark.asyncio
    async def test_download_passes_paths(self) -> None:
        """download_file passes sandbox_path and host_path correctly."""
        mgr = SandboxManager()
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await mgr.download_file("/sandbox/output.json", "/tmp/output.json")

        args = mock_exec.call_args[0]
        assert args[4] == "/sandbox/output.json"
        assert args[5] == "/tmp/output.json"
