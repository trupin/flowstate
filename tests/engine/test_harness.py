"""Tests for Harness protocol, HarnessManager, and executor harness integration.

Validates that:
- HarnessManager.get("claude") returns the default harness
- HarnessManager.get("unknown") raises HarnessNotFoundError
- SubprocessManager and SDKRunner satisfy the Harness Protocol structurally
- FlowExecutor works with harness_mgr parameter (backward compat)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from flowstate.engine.harness import HarnessConfig, HarnessManager, HarnessNotFoundError
from flowstate.engine.subprocess_mgr import (
    JudgeResult,
    StreamEvent,
    StreamEventType,
    SubprocessManager,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeHarness:
    """A minimal fake that matches the Harness Protocol structurally."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.kill_calls: list[str] = []
        self.started_sessions: list[tuple[str, str]] = []
        self.prompt_calls: list[tuple[str, str]] = []
        self.interrupt_calls: list[str] = []

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.calls.append((prompt, workspace, session_id))
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> JudgeResult:
        return JudgeResult(decision="__none__", reasoning="test", confidence=1.0, raw_output="")

    async def kill(self, session_id: str) -> None:
        self.kill_calls.append(session_id)

    async def start_session(self, workspace: str, session_id: str) -> None:
        self.started_sessions.append((workspace, session_id))

    async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]:
        self.prompt_calls.append((session_id, message))
        yield StreamEvent(
            type=StreamEventType.RESULT,
            content={"type": "result", "result": "", "stop_reason": "end_turn"},
            raw='{"type": "result", "result": "", "stop_reason": "end_turn"}',
        )
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def interrupt(self, session_id: str) -> None:
        self.interrupt_calls.append(session_id)


# ---------------------------------------------------------------------------
# Tests: HarnessManager
# ---------------------------------------------------------------------------


class TestHarnessManager:
    """Tests for HarnessManager registry."""

    def test_get_default_returns_claude(self) -> None:
        """get('claude') returns the default harness passed to the constructor."""
        fake = FakeHarness()
        mgr = HarnessManager(default_harness=fake)
        assert mgr.get("claude") is fake

    def test_get_unknown_raises(self) -> None:
        """get('unknown') raises HarnessNotFoundError."""
        fake = FakeHarness()
        mgr = HarnessManager(default_harness=fake)
        with pytest.raises(HarnessNotFoundError) as exc_info:
            mgr.get("unknown_harness")
        assert exc_info.value.name == "unknown_harness"
        assert "unknown_harness" in str(exc_info.value)

    def test_register_and_get(self) -> None:
        """register() adds a harness; get() retrieves it."""
        default = FakeHarness()
        custom = FakeHarness()
        mgr = HarnessManager(default_harness=default)
        mgr.register("custom", custom)
        assert mgr.get("custom") is custom
        # Default still works
        assert mgr.get("claude") is default

    def test_register_overwrites(self) -> None:
        """register() with existing name overwrites the previous entry."""
        old = FakeHarness()
        new = FakeHarness()
        mgr = HarnessManager(default_harness=old)
        mgr.register("claude", new)
        assert mgr.get("claude") is new

    def test_names_property(self) -> None:
        """names returns all registered harness names."""
        mgr = HarnessManager(default_harness=FakeHarness())
        mgr.register("playwright", FakeHarness())
        names = mgr.names
        assert "claude" in names
        assert "playwright" in names

    def test_configs_stored(self) -> None:
        """configs dict is stored for future use (ENGINE-034)."""
        cfg = {"custom": HarnessConfig(command=["my-tool"], env={"KEY": "val"})}
        mgr = HarnessManager(default_harness=FakeHarness(), configs=cfg)
        assert mgr._configs == cfg


# ---------------------------------------------------------------------------
# Tests: Harness Protocol structural satisfaction
# ---------------------------------------------------------------------------


class TestHarnessProtocolSatisfaction:
    """Verify that existing classes match the Harness Protocol structurally."""

    def test_subprocess_manager_satisfies_protocol(self) -> None:
        """SubprocessManager has all 4 methods required by Harness."""
        mgr = SubprocessManager()
        # Check that all protocol methods exist and are callable
        assert callable(getattr(mgr, "run_task", None))
        assert callable(getattr(mgr, "run_task_resume", None))
        assert callable(getattr(mgr, "run_judge", None))
        assert callable(getattr(mgr, "kill", None))

    def test_sdk_runner_satisfies_protocol(self) -> None:
        """SDKRunner has all 4 methods required by Harness."""
        from flowstate.engine.sdk_runner import SDKRunner

        runner = SDKRunner()
        assert callable(getattr(runner, "run_task", None))
        assert callable(getattr(runner, "run_task_resume", None))
        assert callable(getattr(runner, "run_judge", None))
        assert callable(getattr(runner, "kill", None))

    def test_fake_harness_satisfies_protocol(self) -> None:
        """FakeHarness (no inheritance) satisfies Protocol structurally."""
        fake = FakeHarness()
        assert callable(getattr(fake, "run_task", None))
        assert callable(getattr(fake, "run_task_resume", None))
        assert callable(getattr(fake, "run_judge", None))
        assert callable(getattr(fake, "kill", None))
        assert callable(getattr(fake, "start_session", None))
        assert callable(getattr(fake, "prompt", None))
        assert callable(getattr(fake, "interrupt", None))

    def test_harness_manager_accepts_subprocess_manager(self) -> None:
        """HarnessManager constructor accepts SubprocessManager as default_harness."""
        mgr = SubprocessManager()
        hm = HarnessManager(default_harness=mgr)
        assert hm.get("claude") is mgr

    def test_harness_manager_accepts_fake(self) -> None:
        """HarnessManager constructor accepts a duck-typed fake as default_harness."""
        fake = FakeHarness()
        hm = HarnessManager(default_harness=fake)
        assert hm.get("claude") is fake


# ---------------------------------------------------------------------------
# Tests: HarnessNotFoundError
# ---------------------------------------------------------------------------


class TestHarnessNotFoundError:
    """Tests for HarnessNotFoundError exception."""

    def test_stores_name(self) -> None:
        err = HarnessNotFoundError("playwright")
        assert err.name == "playwright"

    def test_message_format(self) -> None:
        err = HarnessNotFoundError("playwright")
        assert str(err) == "Harness 'playwright' not found in registry"


# ---------------------------------------------------------------------------
# Tests: HarnessConfig
# ---------------------------------------------------------------------------


class TestHarnessConfig:
    """Tests for HarnessConfig dataclass."""

    def test_defaults(self) -> None:
        cfg = HarnessConfig()
        assert cfg.command == []
        assert cfg.env is None

    def test_custom_values(self) -> None:
        cfg = HarnessConfig(command=["npx", "playwright", "test"], env={"CI": "true"})
        assert cfg.command == ["npx", "playwright", "test"]
        assert cfg.env == {"CI": "true"}

    def test_mutable_default_isolation(self) -> None:
        """Each HarnessConfig gets its own command list (default_factory)."""
        a = HarnessConfig()
        b = HarnessConfig()
        a.command.append("modified")
        assert b.command == []
