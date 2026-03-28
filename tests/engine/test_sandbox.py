"""Tests for the SandboxManager (persistent sandbox connect-wrapper model)."""

from pathlib import Path

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
