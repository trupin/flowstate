"""Tests for context assembly -- prompt construction, template expansion."""

from __future__ import annotations

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
    expand_templates,
    get_context_mode,
    lumon_plugin_dir,
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
# Tests: build_prompt_handoff
# ---------------------------------------------------------------------------


class TestBuildPromptHandoff:
    def test_build_prompt_handoff(self) -> None:
        """Handoff prompt contains all required sections with curl-based artifact upload."""
        node = _make_node(prompt="Implement the feature")
        prompt = build_prompt_handoff(
            node=node,
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
        prompt = build_prompt_session(node=node)
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
# Tests: Lumon-aware prompt builders
# ---------------------------------------------------------------------------


class TestLumonPromptHandoff:
    def test_lumon_handoff_uses_plugin_submit(self) -> None:
        """Handoff prompt with lumon=True uses flowstate.submit_summary() instead of curl."""
        node = _make_node(prompt="Implement the feature")
        prompt = build_prompt_handoff(
            node=node,
            cwd="/project",
            predecessor_summary="Previous task analyzed the codebase.",
            lumon=True,
        )
        assert "flowstate.submit_summary" in prompt
        assert "curl" not in prompt
        assert "$FLOWSTATE_SERVER_URL" not in prompt
        # Core content is still present
        assert "Context from previous task" in prompt
        assert "Previous task analyzed the codebase." in prompt
        assert "Your task" in prompt
        assert "Working directory" in prompt

    def test_lumon_false_handoff_unchanged(self) -> None:
        """Handoff prompt with lumon=False still uses curl (backwards compatible)."""
        node = _make_node(prompt="Do stuff")
        prompt = build_prompt_handoff(
            node=node, cwd="/project", predecessor_summary="prev", lumon=False
        )
        assert "curl" in prompt
        assert "flowstate.submit_summary" not in prompt


class TestLumonPromptSession:
    def test_lumon_session_uses_plugin_submit(self) -> None:
        """Session prompt with lumon=True uses flowstate.submit_summary() instead of curl."""
        node = _make_node(name="review", prompt="Review the implementation")
        prompt = build_prompt_session(node=node, lumon=True)
        assert "flowstate.submit_summary" in prompt
        assert "curl" not in prompt
        assert "Next task: review" in prompt

    def test_lumon_false_session_unchanged(self) -> None:
        """Session prompt with lumon=False still uses curl."""
        node = _make_node(name="review", prompt="Review")
        prompt = build_prompt_session(node=node, lumon=False)
        assert "curl" in prompt
        assert "flowstate.submit_summary" not in prompt


class TestLumonPromptNone:
    def test_lumon_none_uses_plugin_submit(self) -> None:
        """None-mode prompt with lumon=True uses flowstate.submit_summary()."""
        node = _make_node(prompt="Initialize")
        prompt = build_prompt_none(node=node, cwd="/project", lumon=True)
        assert "flowstate.submit_summary" in prompt
        assert "curl" not in prompt
        assert "Your task" in prompt
        assert "Working directory" in prompt


class TestLumonPromptJoin:
    def test_lumon_join_uses_plugin_submit(self) -> None:
        """Join prompt with lumon=True uses flowstate.submit_summary()."""
        node = _make_node(name="merge", prompt="Merge the results")
        prompt = build_prompt_join(
            node=node,
            cwd="/project",
            member_summaries={"worker_a": "Done", "worker_b": "Also done"},
            lumon=True,
        )
        assert "flowstate.submit_summary" in prompt
        assert "curl" not in prompt
        assert "Context from parallel tasks" in prompt
        assert "### worker_a" in prompt


class TestLumonRoutingInstructions:
    def test_lumon_routing_uses_plugin_submit(self) -> None:
        """Routing instructions with lumon=True use flowstate.submit_decision()."""
        result = build_routing_instructions(
            [("tests_pass", "deploy"), ("tests_fail", "fix")],
            lumon=True,
        )
        assert "flowstate.submit_decision" in result
        assert "curl" not in result
        assert "Routing Decision" in result
        assert '"tests_pass"' in result
        assert "deploy" in result
        assert "__none__" in result

    def test_lumon_false_routing_unchanged(self) -> None:
        """Routing instructions with lumon=False still use curl."""
        result = build_routing_instructions([("done", "next")], lumon=False)
        assert "curl" in result
        assert "flowstate.submit_decision" not in result


class TestLumonCrossFlowInstructions:
    def test_lumon_cross_flow_uses_plugin_submit(self) -> None:
        """Cross-flow instructions with lumon=True use flowstate.submit_output()."""
        result = build_cross_flow_instructions(
            ["deploy_flow", "notify_flow"],
            lumon=True,
        )
        assert "flowstate.submit_output" in result
        assert "curl" not in result
        assert "Cross-flow output" in result
        assert "deploy_flow" in result

    def test_lumon_false_cross_flow_unchanged(self) -> None:
        """Cross-flow instructions with lumon=False still use curl."""
        result = build_cross_flow_instructions(["target_flow"], lumon=False)
        assert "curl" in result
        assert "flowstate.submit_output" not in result


class TestLumonPluginDir:
    def test_lumon_plugin_dir_exists(self) -> None:
        """lumon_plugin_dir() returns a path containing the plugin files."""
        import os

        plugin_dir = lumon_plugin_dir()
        assert os.path.isdir(plugin_dir)
        assert os.path.isfile(os.path.join(plugin_dir, "manifest.lumon"))
        assert os.path.isfile(os.path.join(plugin_dir, "impl.lumon"))
        assert os.path.isfile(os.path.join(plugin_dir, "flowstate_plugin.py"))

    def test_lumon_plugin_dir_is_absolute(self) -> None:
        """lumon_plugin_dir() returns an absolute path."""
        import os

        assert os.path.isabs(lumon_plugin_dir())
