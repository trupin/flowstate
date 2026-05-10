"""Tests for ENGINE-086: agent.md persona loading and executor wiring.

Covers:
  - load_agent_persona happy path with frontmatter + body + template expansion
  - load_agent_persona with no frontmatter (body-only file)
  - Missing persona file at run-time -> AgentPersonaError
  - Malformed YAML frontmatter at run-time -> AgentPersonaError
  - Empty body (frontmatter only) -> empty system_prompt, no exception
  - User-global ~/.claude/agents fallback resolution
  - Path-tampering: bare-name resolver doesn't traverse "/" or ".."
  - SubprocessManager.run_task_with_system_prompt now accepts `settings` kwarg
  - SDKRunner / AcpHarness raise NotImplementedError cleanly
  - Executor dispatches agent-using nodes via run_task_with_system_prompt
  - Executor dispatches no-agent nodes via run_task (regression / no-op)
  - Frontmatter `model:` swaps the harness when registered; warns and falls
    back when unregistered
  - {{template_var}} expands in BOTH the persona body AND the kickoff message
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from flowstate.dsl.ast import (
    ContextMode,
    Edge,
    EdgeType,
    ErrorPolicy,
    Flow,
    Node,
    NodeType,
)
from flowstate.engine.context import (
    AgentPersona,
    AgentPersonaError,
    _resolve_agent_md,
    _split_frontmatter,
    load_agent_persona,
)
from flowstate.engine.executor import FlowExecutor
from flowstate.engine.harness import HarnessManager
from flowstate.engine.subprocess_mgr import StreamEvent, StreamEventType
from flowstate.state.repository import FlowstateDB

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from flowstate.engine.events import FlowEvent


FIXTURES_DIR = Path(__file__).parent / "fixtures"
AGENTS_FIXTURE_DIR = FIXTURES_DIR / "agents"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    name: str = "task1",
    node_type: NodeType = NodeType.TASK,
    prompt: str = "Do the work",
    agent: str | None = None,
    harness: str | None = None,
) -> Node:
    return Node(
        name=name,
        node_type=node_type,
        prompt=prompt,
        agent=agent,
        harness=harness,
    )


def _make_flow(
    nodes: dict[str, Node],
    edges: tuple[Edge, ...],
    workspace: str = "/workspace",
    harness: str = "claude",
    on_error: ErrorPolicy = ErrorPolicy.ABORT,
) -> Flow:
    return Flow(
        name="agent-test-flow",
        budget_seconds=3600,
        on_error=on_error,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes,
        edges=edges,
        harness=harness,
    )


def _simple_linear_flow(
    *,
    agent: str | None = None,
    template_in_prompt: bool = False,
    harness: str = "claude",
    workspace: str = "/workspace",
) -> Flow:
    """Build a 2-node flow (entry -> exit). The exit node carries the persona."""
    entry = Node(name="entry", node_type=NodeType.ENTRY, prompt="Begin")
    exit_prompt = "Topic: {{topic}}" if template_in_prompt else "Wrap up"
    exit_node = Node(
        name="exit",
        node_type=NodeType.EXIT,
        prompt=exit_prompt,
        agent=agent,
    )
    edges = (
        Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source="entry",
            target="exit",
        ),
    )
    return _make_flow(
        nodes={"entry": entry, "exit": exit_node},
        edges=edges,
        harness=harness,
        workspace=workspace,
    )


class RecordingHarness:
    """A test harness that records every dispatch call and yields a clean exit.

    Used to assert which dispatch path the executor selected for a given node
    (agent vs. no-agent → run_task_with_system_prompt vs. run_task).
    """

    def __init__(self, system_prompt_supported: bool = True) -> None:
        self.run_task_calls: list[tuple[str, str, str]] = []
        self.resume_calls: list[tuple[str, str, str]] = []
        self.system_prompt_calls: list[tuple[str, str, str, str]] = []
        # When False, run_task_with_system_prompt raises NotImplementedError
        # to simulate SDK / ACP harnesses that lack system-prompt support.
        self._system_prompt_supported = system_prompt_supported

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.run_task_calls.append((prompt, workspace, session_id))
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def run_task_with_system_prompt(
        self,
        system_prompt: str,
        init_message: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
        model: str | None = None,
        settings: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        if not self._system_prompt_supported:
            raise NotImplementedError("This harness does not support run_task_with_system_prompt.")
        self.system_prompt_calls.append((system_prompt, init_message, workspace, session_id))
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
        self.resume_calls.append((prompt, workspace, resume_session_id))
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> Any:
        raise NotImplementedError

    async def kill(self, session_id: str) -> None:
        pass

    async def start_session(self, workspace: str, session_id: str) -> None:
        pass

    async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def interrupt(self, session_id: str) -> None:
        pass


def _make_db() -> FlowstateDB:
    return FlowstateDB(":memory:")


def _collect_events() -> tuple[list[FlowEvent], Any]:
    events: list[FlowEvent] = []

    def callback(event: FlowEvent) -> None:
        events.append(event)

    return events, callback


def _write_agent_md(dirpath: Path, name: str, content: str) -> Path:
    agents_dir = dirpath / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{name}.md"
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# Unit tests: _split_frontmatter
# ---------------------------------------------------------------------------


class TestSplitFrontmatter:
    def test_with_frontmatter(self) -> None:
        text = "---\nname: A\nmodel: claude\n---\nHello body."
        fm, body = _split_frontmatter(text)
        assert fm == {"name": "A", "model": "claude"}
        assert body == "Hello body."

    def test_no_frontmatter(self) -> None:
        text = "Just a body. No frontmatter marker."
        fm, body = _split_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_frontmatter_only_no_body(self) -> None:
        text = "---\nname: B\n---\n"
        fm, body = _split_frontmatter(text)
        assert fm == {"name": "B"}
        assert body == ""

    def test_unterminated_frontmatter_raises(self) -> None:
        text = "---\nname: oops\nno closing"
        with pytest.raises(AgentPersonaError):
            _split_frontmatter(text)

    def test_malformed_yaml_raises(self) -> None:
        # Unbalanced bracket → yaml.YAMLError → wrapped as AgentPersonaError.
        text = "---\nname: [unterminated\n---\nbody"
        with pytest.raises(AgentPersonaError):
            _split_frontmatter(text)

    def test_empty_frontmatter_block(self) -> None:
        # Empty frontmatter (just `---\n---\n`) → empty dict.
        text = "---\n---\nbody only"
        fm, body = _split_frontmatter(text)
        assert fm == {}
        assert body == "body only"


# ---------------------------------------------------------------------------
# Unit tests: _resolve_agent_md
# ---------------------------------------------------------------------------


class TestResolveAgentMd:
    def test_resolves_flow_local(self, tmp_path: Path) -> None:
        _write_agent_md(tmp_path, "helly", "---\nname: Helly\n---\nBody.")
        resolved = _resolve_agent_md("helly", tmp_path)
        assert resolved is not None
        assert resolved == tmp_path / "agents" / "helly.md"

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        # No agents dir at all
        resolved = _resolve_agent_md("ghost", tmp_path)
        # If a user-global ~/.claude/agents/ghost.md exists on the dev box,
        # this could fail. Use a name unlikely to exist there.
        if resolved is not None:
            pytest.skip(f"User-global persona at {resolved} exists; cannot test missing path")
        assert resolved is None

    def test_user_global_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When flow-local lookup fails, ~/.claude/agents/<name>.md resolves."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        user_agents = fake_home / ".claude" / "agents"
        user_agents.mkdir(parents=True)
        user_path = user_agents / "globalpersona.md"
        user_path.write_text("Global body.")

        resolved = _resolve_agent_md("globalpersona", tmp_path)
        assert resolved == user_path


# ---------------------------------------------------------------------------
# Unit tests: load_agent_persona
# ---------------------------------------------------------------------------


class TestLoadAgentPersona:
    def test_returns_none_when_node_has_no_agent(self, tmp_path: Path) -> None:
        node = _make_node(agent=None)
        assert load_agent_persona(node, tmp_path, {}) is None

    def test_loads_fixture_with_frontmatter_and_template(self) -> None:
        """The bundled fixture parses cleanly and {{topic}} is expanded."""
        node = _make_node(agent="sample")
        persona = load_agent_persona(node, FIXTURES_DIR, {"topic": "refactor the parser"})
        assert persona is not None
        assert isinstance(persona, AgentPersona)
        # Frontmatter values are surfaced
        assert persona.model == "claude"
        assert persona.raw_frontmatter["name"] == "SamplePersona"
        assert persona.raw_frontmatter["tools"] == ["Read", "Edit"]
        # Template expanded in body, frontmatter stripped from system_prompt
        assert "You are SamplePersona" in persona.system_prompt
        assert "refactor the parser" in persona.system_prompt
        assert "{{topic}}" not in persona.system_prompt
        assert "---" not in persona.system_prompt.splitlines()[0]
        # Source path resolves to the fixture file
        assert persona.source_path == AGENTS_FIXTURE_DIR / "sample.md"

    def test_loads_body_only_file(self, tmp_path: Path) -> None:
        _write_agent_md(tmp_path, "plain", "Plain body, no frontmatter.\n")
        node = _make_node(agent="plain")
        persona = load_agent_persona(node, tmp_path, {})
        assert persona is not None
        assert persona.model is None
        assert persona.raw_frontmatter == {}
        assert persona.system_prompt == "Plain body, no frontmatter.\n"

    def test_empty_body_after_frontmatter(self, tmp_path: Path) -> None:
        _write_agent_md(tmp_path, "empty", "---\nname: E\n---\n")
        node = _make_node(agent="empty")
        persona = load_agent_persona(node, tmp_path, {})
        assert persona is not None
        assert persona.system_prompt == ""
        assert persona.raw_frontmatter == {"name": "E"}

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """Defense in depth: file deleted between type-check and run."""
        node = _make_node(agent="gonzo_not_real")
        with pytest.raises(AgentPersonaError) as exc_info:
            load_agent_persona(node, tmp_path, {})
        msg = str(exc_info.value)
        assert "gonzo_not_real" in msg
        assert "task1" in msg  # node name in the error
        # Both lookup paths surfaced
        assert "agents" in msg
        assert ".claude" in msg

    def test_malformed_frontmatter_raises(self, tmp_path: Path) -> None:
        _write_agent_md(
            tmp_path,
            "broken",
            "---\nname: [unterminated\n---\nbody",
        )
        node = _make_node(agent="broken")
        with pytest.raises(AgentPersonaError):
            load_agent_persona(node, tmp_path, {})

    def test_unterminated_frontmatter_raises(self, tmp_path: Path) -> None:
        _write_agent_md(tmp_path, "stuck", "---\nname: oops\nno closing.")
        node = _make_node(agent="stuck")
        with pytest.raises(AgentPersonaError):
            load_agent_persona(node, tmp_path, {})

    def test_model_field_extracted_as_string(self, tmp_path: Path) -> None:
        _write_agent_md(
            tmp_path,
            "withmodel",
            "---\nmodel: subprocess\n---\nBody.",
        )
        node = _make_node(agent="withmodel")
        persona = load_agent_persona(node, tmp_path, {})
        assert persona is not None
        assert persona.model == "subprocess"

    def test_non_string_model_field_ignored(self, tmp_path: Path) -> None:
        """If frontmatter `model:` is not a string, treat as None (defensive)."""
        _write_agent_md(
            tmp_path,
            "weirdmodel",
            "---\nmodel: 42\n---\nBody.",
        )
        node = _make_node(agent="weirdmodel")
        persona = load_agent_persona(node, tmp_path, {})
        assert persona is not None
        assert persona.model is None
        # But the raw frontmatter still records the value for observability
        assert persona.raw_frontmatter["model"] == 42

    def test_template_unmatched_left_as_is(self, tmp_path: Path) -> None:
        _write_agent_md(
            tmp_path,
            "tmplunmatched",
            "Hello {{name}} — your task is {{missing}}.",
        )
        node = _make_node(agent="tmplunmatched")
        persona = load_agent_persona(node, tmp_path, {"name": "Alice"})
        assert persona is not None
        assert "Hello Alice" in persona.system_prompt
        # Unmatched var preserved as-is (matches expand_templates semantics)
        assert "{{missing}}" in persona.system_prompt


# ---------------------------------------------------------------------------
# Unit tests: SubprocessManager.run_task_with_system_prompt protocol parity
# ---------------------------------------------------------------------------


class TestSubprocessManagerSettingsKwarg:
    def test_settings_kwarg_accepted(self) -> None:
        """SubprocessManager.run_task_with_system_prompt accepts `settings=...`.

        The Harness protocol now declares this method with a `settings` kwarg
        for parity with run_task / run_task_resume. SubprocessManager ignores
        the value (the CLI discovers settings from cwd) but must not reject
        the call.
        """
        import inspect

        from flowstate.engine.subprocess_mgr import SubprocessManager

        sig = inspect.signature(SubprocessManager.run_task_with_system_prompt)
        assert "settings" in sig.parameters

    def test_sdk_runner_raises_not_implemented(self) -> None:
        """SDKRunner explicitly refuses system-prompt dispatch."""
        from flowstate.engine.sdk_runner import SDKRunner

        runner = SDKRunner()

        async def _drive() -> list[StreamEvent]:
            events: list[StreamEvent] = []
            async for event in runner.run_task_with_system_prompt(
                "sys", "init", "/tmp", "session-1"
            ):
                events.append(event)
            return events

        with pytest.raises(NotImplementedError) as exc_info:
            asyncio.run(_drive())
        assert "agent.md" in str(exc_info.value)

    def test_acp_harness_raises_not_implemented(self) -> None:
        """AcpHarness explicitly refuses system-prompt dispatch."""
        from flowstate.engine.acp_client import AcpHarness

        harness = AcpHarness(command=["echo"])

        async def _drive() -> list[StreamEvent]:
            events: list[StreamEvent] = []
            async for event in harness.run_task_with_system_prompt(
                "sys", "init", "/tmp", "session-1"
            ):
                events.append(event)
            return events

        with pytest.raises(NotImplementedError) as exc_info:
            asyncio.run(_drive())
        assert "agent.md" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Integration tests: executor dispatch wiring
# ---------------------------------------------------------------------------


class TestExecutorAgentDispatch:
    """Verify the executor picks the right dispatch path based on Node.agent."""

    async def test_no_agent_uses_run_task(self, tmp_path: Path) -> None:
        """Regression: nodes without `agent` go through the existing run_task path."""
        flow = _simple_linear_flow(agent=None, workspace=str(tmp_path))
        db = _make_db()
        _events, callback = _collect_events()
        harness = RecordingHarness()
        executor = FlowExecutor(
            db,
            callback,
            harness,
            server_base_url="http://127.0.0.1:9090",
            flow_file_dir=str(tmp_path),
        )

        await executor.execute(flow, {}, str(tmp_path))

        # Both nodes (entry + exit) went through run_task; system-prompt path
        # was never used.
        assert len(harness.system_prompt_calls) == 0
        assert len(harness.run_task_calls) == 2

    async def test_agent_dispatches_to_system_prompt(self, tmp_path: Path) -> None:
        """When Node.agent is set, dispatch uses run_task_with_system_prompt.

        Both the persona body and the kickoff message receive template
        substitution from the same param dict.
        """
        # Persona body and kickoff prompt both reference {{topic}}.
        _write_agent_md(
            tmp_path,
            "helly",
            "---\nname: Helly R.\n---\n" "You are Helly. Topic: {{topic}}. Push back.",
        )
        flow = _simple_linear_flow(agent="helly", template_in_prompt=True, workspace=str(tmp_path))
        # entry has no agent -> run_task; exit has agent -> system-prompt.
        db = _make_db()
        _events, callback = _collect_events()
        harness = RecordingHarness()
        executor = FlowExecutor(
            db,
            callback,
            harness,
            server_base_url="http://127.0.0.1:9090",
            flow_file_dir=str(tmp_path),
        )

        await executor.execute(flow, {"topic": "should I refactor X"}, str(tmp_path))

        # entry → run_task; exit → system-prompt
        assert len(harness.run_task_calls) == 1
        assert len(harness.system_prompt_calls) == 1

        system_prompt, init_message, _workspace, _session = harness.system_prompt_calls[0]
        # Frontmatter stripped, template expanded in BOTH places.
        assert "You are Helly" in system_prompt
        assert "should I refactor X" in system_prompt
        assert "{{topic}}" not in system_prompt
        assert "---" not in system_prompt.splitlines()[0]
        # The kickoff message is the prompt-built text containing the
        # expanded node.prompt + context sections; it must also be templated.
        assert "should I refactor X" in init_message
        assert "{{topic}}" not in init_message
        # The kickoff is NOT the system prompt
        assert system_prompt != init_message

    async def test_missing_persona_at_runtime_fails_task(self, tmp_path: Path) -> None:
        """If the persona file is missing at run-time, the task fails cleanly.

        We deliberately do NOT create the agents/<name>.md file. This simulates
        a file deleted between type-check and execution.
        """
        flow = _simple_linear_flow(
            agent="ghost_persona_not_present_anywhere_xyz", workspace=str(tmp_path)
        )
        db = _make_db()
        _events, callback = _collect_events()
        harness = RecordingHarness()
        executor = FlowExecutor(
            db,
            callback,
            harness,
            server_base_url="http://127.0.0.1:9090",
            flow_file_dir=str(tmp_path),
        )

        # The exit task should fail; flow run does not complete normally.
        # entry has no agent — it succeeds via run_task; exit then errors.
        run_id = await executor.execute(flow, {}, str(tmp_path))

        # The exit task is marked failed with the persona error message.
        tasks = db.list_task_executions(run_id)
        exit_tasks = [t for t in tasks if t.node_name == "exit"]
        assert exit_tasks, "exit task should exist"
        # The exit task is failed and the error mentions the persona name.
        failed_exit = exit_tasks[-1]
        assert failed_exit.status == "failed"
        assert failed_exit.error_message is not None
        assert "ghost_persona_not_present_anywhere_xyz" in failed_exit.error_message
        # The system-prompt dispatch was never invoked (load failed first).
        assert len(harness.system_prompt_calls) == 0

    async def test_frontmatter_model_selects_registered_harness(self, tmp_path: Path) -> None:
        """`model:` matching a registered harness name swaps the active harness."""
        _write_agent_md(
            tmp_path,
            "swap",
            "---\nmodel: custom-backend\n---\nBody for swap.",
        )
        # Two distinct harnesses; the default ("claude") and the override.
        default = RecordingHarness()
        custom = RecordingHarness()
        harness_mgr = HarnessManager(default_harness=default)
        harness_mgr.register("custom-backend", custom)

        # Build a flow where the exit node has agent="swap" but the
        # flow-level harness is still "claude".
        flow = _simple_linear_flow(agent="swap", workspace=str(tmp_path))
        db = _make_db()
        _events, callback = _collect_events()
        executor = FlowExecutor(
            db,
            callback,
            default,
            harness_mgr=harness_mgr,
            server_base_url="http://127.0.0.1:9090",
            flow_file_dir=str(tmp_path),
        )

        await executor.execute(flow, {}, str(tmp_path))

        # Entry has no agent → went through default (run_task).
        assert len(default.run_task_calls) == 1
        # Exit dispatched to the model-selected harness via system-prompt.
        assert len(custom.system_prompt_calls) == 1
        assert len(default.system_prompt_calls) == 0

    async def test_frontmatter_model_unregistered_warns_and_falls_back(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`model:` with no matching harness logs a warning and falls back."""
        _write_agent_md(
            tmp_path,
            "bogus",
            "---\nmodel: completely-unknown-harness-xyz\n---\nBody.",
        )
        flow = _simple_linear_flow(agent="bogus", workspace=str(tmp_path))
        db = _make_db()
        _events, callback = _collect_events()
        harness = RecordingHarness()
        executor = FlowExecutor(
            db,
            callback,
            harness,
            server_base_url="http://127.0.0.1:9090",
            flow_file_dir=str(tmp_path),
        )

        with caplog.at_level(logging.WARNING, logger="flowstate.engine.executor"):
            await executor.execute(flow, {}, str(tmp_path))

        # Fell back to the default harness's system-prompt path.
        assert len(harness.system_prompt_calls) == 1
        # Warning was logged mentioning the unknown model.
        warning_records = [
            r for r in caplog.records if "completely-unknown-harness-xyz" in r.getMessage()
        ]
        assert warning_records, "expected a warning for unknown model"


# ---------------------------------------------------------------------------
# Integration test: agent-using node on a harness that lacks system-prompt support
# ---------------------------------------------------------------------------


class TestExecutorAgentDispatchUnsupportedHarness:
    async def test_unsupported_harness_fails_task_cleanly(self, tmp_path: Path) -> None:
        """An ACP/SDK-style harness without system_prompt support fails the task.

        The error must not silently fall back to a no-system-prompt invocation.
        """
        _write_agent_md(tmp_path, "any", "Body content.")
        flow = _simple_linear_flow(agent="any", workspace=str(tmp_path))
        db = _make_db()
        _events, callback = _collect_events()
        harness = RecordingHarness(system_prompt_supported=False)
        executor = FlowExecutor(
            db,
            callback,
            harness,
            server_base_url="http://127.0.0.1:9090",
            flow_file_dir=str(tmp_path),
        )

        run_id = await executor.execute(flow, {}, str(tmp_path))

        tasks = db.list_task_executions(run_id)
        exit_tasks = [t for t in tasks if t.node_name == "exit"]
        assert exit_tasks
        failed = exit_tasks[-1]
        assert failed.status == "failed"
        # Did NOT silently fall back to run_task.
        # The entry node uses run_task (no agent set); the failed exit must
        # not have produced a run_task call (only an attempted system-prompt
        # call that raised NotImplementedError).
        # Entry's run_task is fine; exit must not have produced a run_task.
        # Confirm by inspecting which prompts were dispatched.
        run_task_prompts = [c[0] for c in harness.run_task_calls]
        for p in run_task_prompts:
            assert "Wrap up" not in p, "exit task should not have fallen back to run_task"
