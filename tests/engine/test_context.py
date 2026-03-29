"""Tests for context assembly -- task dirs, prompt construction, template expansion."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstate.dsl.ast import ContextMode, Edge, EdgeConfig, EdgeType, Flow, Node, NodeType
from flowstate.engine.context import (
    CwdResolutionError,
    build_cross_flow_instructions,
    build_prompt_handoff,
    build_prompt_join,
    build_prompt_none,
    build_prompt_session,
    build_routing_instructions,
    create_task_dir,
    expand_templates,
    get_context_mode,
    read_summary,
    resolve_cwd,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    name: str = "analyze",
    prompt: str = "Analyze the code",
    cwd: str | None = None,
) -> Node:
    return Node(name=name, node_type=NodeType.TASK, prompt=prompt, cwd=cwd)


def _make_flow(
    workspace: str | None = "/project",
    context: ContextMode = ContextMode.HANDOFF,
) -> Flow:
    return Flow(
        name="test_flow",
        budget_seconds=3600,
        on_error="pause",  # type: ignore[arg-type]
        context=context,
        workspace=workspace,
    )


def _make_edge(context: ContextMode | None = None) -> Edge:
    return Edge(
        edge_type=EdgeType.UNCONDITIONAL,
        source="a",
        target="b",
        config=EdgeConfig(context=context),
    )


# ---------------------------------------------------------------------------
# Tests: create_task_dir
# ---------------------------------------------------------------------------


class TestCreateTaskDir:
    def test_create_task_dir(self, tmp_path: Path) -> None:
        """Creates <tmp>/tasks/<name>-1/ and returns the correct path."""
        run_dir = str(tmp_path / "run-abc")
        result = create_task_dir(run_dir, "analyze", 1)
        assert Path(result).exists()
        assert result.endswith("analyze-1")

    def test_create_task_dir_creates_parents(self, tmp_path: Path) -> None:
        """Non-existent run_data_dir is created along with parents."""
        run_dir = str(tmp_path / "deep" / "nested" / "run-xyz")
        result = create_task_dir(run_dir, "build", 1)
        assert Path(result).exists()
        assert Path(run_dir).exists()

    def test_create_task_dir_generation_2(self, tmp_path: Path) -> None:
        """Generation 2 creates a directory ending with <name>-2."""
        run_dir = str(tmp_path / "run-gen")
        result = create_task_dir(run_dir, "implement", 2)
        assert result.endswith("implement-2")
        assert Path(result).is_dir()

    def test_create_task_dir_idempotent(self, tmp_path: Path) -> None:
        """Calling twice with the same args does not raise."""
        run_dir = str(tmp_path / "run-idem")
        result1 = create_task_dir(run_dir, "test", 1)
        result2 = create_task_dir(run_dir, "test", 1)
        assert result1 == result2
        assert Path(result1).is_dir()


# ---------------------------------------------------------------------------
# Tests: build_prompt_handoff
# ---------------------------------------------------------------------------


class TestBuildPromptHandoff:
    def test_build_prompt_handoff(self) -> None:
        """Handoff prompt contains all required sections with curl-based artifact upload."""
        node = _make_node(prompt="Implement the feature")
        prompt = build_prompt_handoff(
            node=node,
            task_dir="/home/user/.flowstate/runs/r1/tasks/implement-1",
            cwd="/project",
            predecessor_summary="Previous task analyzed the codebase.",
        )
        assert "Context from previous task" in prompt
        assert "Previous task analyzed the codebase." in prompt
        assert "Your task" in prompt
        assert "Implement the feature" in prompt
        assert "Working directory" in prompt
        assert "/project" in prompt
        assert "Task coordination" in prompt
        # Should use curl-based artifact upload with env var references
        assert "$FLOWSTATE_SERVER_URL" in prompt
        assert "$FLOWSTATE_RUN_ID" in prompt
        assert "$FLOWSTATE_TASK_ID" in prompt
        assert "curl" in prompt
        assert "artifacts/summary" in prompt

    def test_build_prompt_handoff_no_summary(self) -> None:
        """Handoff with predecessor_summary=None includes fallback message."""
        node = _make_node()
        prompt = build_prompt_handoff(
            node=node,
            task_dir="/task/dir",
            cwd="/project",
            predecessor_summary=None,
        )
        assert "No summary available from predecessor task" in prompt
        assert "Context from previous task" in prompt

    def test_build_prompt_handoff_empty_summary(self) -> None:
        """Handoff with empty string summary includes the empty string (no placeholder)."""
        node = _make_node()
        prompt = build_prompt_handoff(
            node=node,
            task_dir="/task/dir",
            cwd="/project",
            predecessor_summary="",
        )
        assert "Context from previous task" in prompt
        # The fallback message should NOT appear since summary is "" not None
        assert "No summary available" not in prompt


# ---------------------------------------------------------------------------
# Tests: build_prompt_session
# ---------------------------------------------------------------------------


class TestBuildPromptSession:
    def test_build_prompt_session(self) -> None:
        """Session prompt contains node name, prompt, and curl-based summary upload."""
        node = _make_node(name="review", prompt="Review the implementation")
        prompt = build_prompt_session(
            node=node,
            task_dir="/home/user/.flowstate/runs/r1/tasks/review-1",
        )
        assert "Next task: review" in prompt
        assert "Review the implementation" in prompt
        # Should use curl-based artifact upload with env var references
        assert "$FLOWSTATE_SERVER_URL" in prompt
        assert "$FLOWSTATE_RUN_ID" in prompt
        assert "$FLOWSTATE_TASK_ID" in prompt
        assert "curl" in prompt
        assert "artifacts/summary" in prompt
        # Session mode should NOT contain these sections
        assert "Context from previous task" not in prompt
        assert "Working directory" not in prompt


# ---------------------------------------------------------------------------
# Tests: build_prompt_none
# ---------------------------------------------------------------------------


class TestBuildPromptNone:
    def test_build_prompt_none(self) -> None:
        """None-mode prompt contains task and curl-based summary upload but no predecessor context."""
        node = _make_node(prompt="Initialize the project")
        prompt = build_prompt_none(
            node=node,
            task_dir="/task/init-1",
            cwd="/project",
        )
        assert "Your task" in prompt
        assert "Initialize the project" in prompt
        assert "Working directory" in prompt
        # Should use curl-based artifact upload
        assert "$FLOWSTATE_SERVER_URL" in prompt
        assert "curl" in prompt
        assert "artifacts/summary" in prompt
        # Should NOT contain predecessor context
        assert "Context from previous task" not in prompt
        assert "Context from parallel tasks" not in prompt


# ---------------------------------------------------------------------------
# Tests: build_prompt_join
# ---------------------------------------------------------------------------


class TestBuildPromptJoin:
    def test_build_prompt_join(self) -> None:
        """Join prompt aggregates member summaries under named sections."""
        node = _make_node(name="merge", prompt="Merge the results")
        prompt = build_prompt_join(
            node=node,
            task_dir="/task/merge-1",
            cwd="/project",
            member_summaries={
                "worker_a": "Did analysis",
                "worker_b": "Did implementation",
            },
        )
        assert "Context from parallel tasks" in prompt
        assert "### worker_a" in prompt
        assert "Did analysis" in prompt
        assert "### worker_b" in prompt
        assert "Did implementation" in prompt
        assert "Your task" in prompt
        assert "Merge the results" in prompt
        # Should use curl-based artifact upload
        assert "$FLOWSTATE_SERVER_URL" in prompt
        assert "curl" in prompt
        assert "artifacts/summary" in prompt

    def test_build_prompt_join_missing_summary(self) -> None:
        """Join with one member missing summary includes fallback for that member."""
        node = _make_node(name="merge", prompt="Merge")
        prompt = build_prompt_join(
            node=node,
            task_dir="/task/merge-1",
            cwd="/project",
            member_summaries={
                "worker_a": "Did analysis",
                "worker_b": None,
            },
        )
        assert "### worker_a" in prompt
        assert "Did analysis" in prompt
        assert "### worker_b" in prompt
        assert "(No summary available)" in prompt


# ---------------------------------------------------------------------------
# Tests: expand_templates
# ---------------------------------------------------------------------------


class TestExpandTemplates:
    def test_expand_templates_string(self) -> None:
        """Expand {{repo}} with a string value."""
        result = expand_templates("Clone {{repo}}", {"repo": "my-repo"})
        assert result == "Clone my-repo"

    def test_expand_templates_number(self) -> None:
        """Expand {{count}} with a numeric value."""
        result = expand_templates("Run {{count}} times", {"count": 42})
        assert result == "Run 42 times"

    def test_expand_templates_bool(self) -> None:
        """Expand {{verbose}} with a boolean value."""
        result = expand_templates("Verbose: {{verbose}}", {"verbose": True})
        assert result == "Verbose: True"

    def test_expand_templates_bool_false(self) -> None:
        """Expand {{verbose}} with False."""
        result = expand_templates("Verbose: {{verbose}}", {"verbose": False})
        assert result == "Verbose: False"

    def test_expand_templates_multiple(self) -> None:
        """Expand text with multiple different params."""
        result = expand_templates(
            "Deploy {{app}} to {{env}} (v{{version}})",
            {"app": "myapp", "env": "prod", "version": "2.0"},
        )
        assert result == "Deploy myapp to prod (v2.0)"

    def test_expand_templates_unknown_var(self) -> None:
        """Unknown template variables are left as-is."""
        result = expand_templates("Hello {{unknown}}", {})
        assert result == "Hello {{unknown}}"

    def test_expand_templates_with_spaces(self) -> None:
        """Whitespace inside braces is tolerated."""
        result = expand_templates("Clone {{ repo }}", {"repo": "my-repo"})
        assert result == "Clone my-repo"

    def test_expand_templates_no_templates(self) -> None:
        """Text with no templates is returned unchanged."""
        result = expand_templates("Just plain text", {"repo": "ignored"})
        assert result == "Just plain text"

    def test_expand_templates_repeated_param(self) -> None:
        """Same param used multiple times is expanded everywhere."""
        result = expand_templates("{{x}} and {{x}}", {"x": "val"})
        assert result == "val and val"


# ---------------------------------------------------------------------------
# Tests: get_context_mode
# ---------------------------------------------------------------------------


class TestGetContextMode:
    def test_get_context_mode_edge_override(self) -> None:
        """Edge-level context overrides flow-level default."""
        edge = _make_edge(context=ContextMode.SESSION)
        flow = _make_flow(context=ContextMode.HANDOFF)
        assert get_context_mode(edge, flow) == ContextMode.SESSION

    def test_get_context_mode_flow_default(self) -> None:
        """Edge has context=None -> flow-level default is used."""
        edge = _make_edge(context=None)
        flow = _make_flow(context=ContextMode.NONE)
        assert get_context_mode(edge, flow) == ContextMode.NONE

    def test_get_context_mode_both_handoff(self) -> None:
        """Both edge and flow are handoff -> handoff returned."""
        edge = _make_edge(context=ContextMode.HANDOFF)
        flow = _make_flow(context=ContextMode.HANDOFF)
        assert get_context_mode(edge, flow) == ContextMode.HANDOFF


# ---------------------------------------------------------------------------
# Tests: resolve_cwd
# ---------------------------------------------------------------------------


class TestResolveCwd:
    def test_resolve_cwd_node_cwd(self) -> None:
        """Node has cwd set -> node.cwd is returned (overrides flow workspace)."""
        node = _make_node(cwd="/node/specific")
        flow = _make_flow(workspace="/project")
        assert resolve_cwd(node, flow) == "/node/specific"

    def test_resolve_cwd_flow_workspace(self) -> None:
        """Node has cwd=None -> flow.workspace is returned."""
        node = _make_node(cwd=None)
        flow = _make_flow(workspace="/project")
        assert resolve_cwd(node, flow) == "/project"

    def test_resolve_cwd_neither(self) -> None:
        """Neither set -> CwdResolutionError is raised."""
        node = _make_node(cwd=None)
        flow = _make_flow(workspace=None)
        with pytest.raises(CwdResolutionError, match="No working directory"):
            resolve_cwd(node, flow)


# ---------------------------------------------------------------------------
# Tests: read_summary
# ---------------------------------------------------------------------------


class TestReadSummary:
    def test_read_summary_exists(self, tmp_path: Path) -> None:
        """Read SUMMARY.md that exists in the task dir."""
        summary_file = tmp_path / "SUMMARY.md"
        summary_file.write_text("I did the thing.\n- Changed file A\n- Result: success\n")

        result = read_summary(str(tmp_path))
        assert result is not None
        assert "I did the thing." in result

    def test_read_summary_missing(self, tmp_path: Path) -> None:
        """Read SUMMARY.md when it does not exist -> None."""
        result = read_summary(str(tmp_path))
        assert result is None

    def test_read_summary_empty(self, tmp_path: Path) -> None:
        """Read an empty SUMMARY.md -> returns empty string (not None)."""
        summary_file = tmp_path / "SUMMARY.md"
        summary_file.write_text("")

        result = read_summary(str(tmp_path))
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: build_routing_instructions
# ---------------------------------------------------------------------------


class TestBuildRoutingInstructions:
    def test_build_routing_instructions_curl_format(self) -> None:
        """Routing instructions use curl POST with env var references."""
        result = build_routing_instructions(
            [
                ("tests_pass", "deploy"),
                ("tests_fail", "fix"),
            ]
        )
        assert "Routing Decision" in result
        assert '"tests_pass"' in result
        assert "deploy" in result
        assert '"tests_fail"' in result
        assert "fix" in result
        assert "$FLOWSTATE_SERVER_URL" in result
        assert "$FLOWSTATE_RUN_ID" in result
        assert "$FLOWSTATE_TASK_ID" in result
        assert "curl" in result
        assert "artifacts/decision" in result
        assert "Content-Type: application/json" in result

    def test_build_routing_instructions_no_task_dir(self) -> None:
        """Routing instructions do not reference a filesystem path."""
        result = build_routing_instructions([("done", "next")])
        # Should not contain any filesystem path patterns
        assert "Write a JSON file to" not in result
        assert "DECISION.json" not in result

    def test_build_routing_instructions_none_fallback(self) -> None:
        """Routing instructions include __none__ fallback."""
        result = build_routing_instructions([("done", "next")])
        assert "__none__" in result


# ---------------------------------------------------------------------------
# Tests: build_cross_flow_instructions
# ---------------------------------------------------------------------------


class TestBuildCrossFlowInstructions:
    def test_build_cross_flow_instructions_curl_format(self) -> None:
        """Cross-flow instructions use curl POST with env var references."""
        result = build_cross_flow_instructions(["deploy_flow", "notify_flow"])
        assert "Cross-flow output" in result
        assert "deploy_flow" in result
        assert "notify_flow" in result
        assert "$FLOWSTATE_SERVER_URL" in result
        assert "$FLOWSTATE_RUN_ID" in result
        assert "$FLOWSTATE_TASK_ID" in result
        assert "curl" in result
        assert "artifacts/output" in result
        assert "Content-Type: application/json" in result

    def test_build_cross_flow_instructions_no_filesystem(self) -> None:
        """Cross-flow instructions do not reference filesystem paths."""
        result = build_cross_flow_instructions(["target_flow"])
        assert "OUTPUT.json file" not in result
        assert "task coordination directory" not in result


# ---------------------------------------------------------------------------
# Tests: _resolve_server_url (via FlowExecutor)
# ---------------------------------------------------------------------------


class TestResolveServerUrl:
    @staticmethod
    def _make_executor(server_base_url: str | None) -> object:
        """Create a minimal FlowExecutor for testing _resolve_server_url."""
        from unittest.mock import MagicMock

        from flowstate.engine.executor import FlowExecutor

        return FlowExecutor(
            db=MagicMock(),
            event_callback=lambda _: None,
            harness=MagicMock(),
            server_base_url=server_base_url,
        )

    def test_resolve_server_url_host(self) -> None:
        """Host mode returns the server URL unchanged."""
        executor = self._make_executor("http://127.0.0.1:9090")
        assert executor._resolve_server_url(use_sandbox=False) == "http://127.0.0.1:9090"  # type: ignore[union-attr]

    def test_resolve_server_url_sandbox(self) -> None:
        """Sandbox mode replaces hostname with host.docker.internal."""
        executor = self._make_executor("http://127.0.0.1:9090")
        assert executor._resolve_server_url(use_sandbox=True) == "http://host.docker.internal:9090"  # type: ignore[union-attr]

    def test_resolve_server_url_no_port(self) -> None:
        """Sandbox mode with no explicit port still replaces hostname."""
        executor = self._make_executor("http://127.0.0.1")
        assert executor._resolve_server_url(use_sandbox=True) == "http://host.docker.internal"  # type: ignore[union-attr]

    def test_resolve_server_url_default_when_none(self) -> None:
        """When server_base_url is None, falls back to default."""
        executor = self._make_executor(None)
        assert executor._resolve_server_url(use_sandbox=False) == "http://127.0.0.1:9090"  # type: ignore[union-attr]

    def test_resolve_server_url_default_sandbox(self) -> None:
        """When server_base_url is None in sandbox mode, uses default with docker host."""
        executor = self._make_executor(None)
        assert executor._resolve_server_url(use_sandbox=True) == "http://host.docker.internal:9090"  # type: ignore[union-attr]
