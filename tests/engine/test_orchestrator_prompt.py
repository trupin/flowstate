"""Tests for orchestrator prompt template -- flow serialization and system prompt."""

from __future__ import annotations

from flowstate.dsl.ast import (
    ContextMode,
    Edge,
    EdgeType,
    ErrorPolicy,
    Flow,
    Node,
    NodeType,
)
from flowstate.engine.context import build_orchestrator_system_prompt, serialize_flow_graph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    name: str,
    prompt: str = "Do the thing",
    node_type: NodeType = NodeType.TASK,
) -> Node:
    return Node(name=name, node_type=node_type, prompt=prompt)


def _make_flow(
    name: str = "test_flow",
    nodes: dict[str, Node] | None = None,
    edges: tuple[Edge, ...] = (),
    workspace: str = "/project",
) -> Flow:
    return Flow(
        name=name,
        budget_seconds=3600,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        workspace=workspace,
        nodes=nodes or {},
        edges=edges,
    )


def _linear_flow() -> Flow:
    """3-node linear flow: start -> analyze -> deploy."""
    nodes = {
        "start": _make_node("start", "Initialize the project", NodeType.ENTRY),
        "analyze": _make_node("analyze", "Analyze the codebase"),
        "deploy": _make_node("deploy", "Deploy to production", NodeType.EXIT),
    }
    edges = (
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="analyze"),
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="analyze", target="deploy"),
    )
    return _make_flow(name="linear_flow", nodes=nodes, edges=edges)


def _conditional_flow() -> Flow:
    """Flow with conditional edges: review -> deploy (pass) or fix (fail)."""
    nodes = {
        "review": _make_node("review", "Review the code"),
        "deploy": _make_node("deploy", "Deploy to production", NodeType.EXIT),
        "fix": _make_node("fix", "Fix the issues"),
    }
    edges = (
        Edge(
            edge_type=EdgeType.CONDITIONAL,
            source="review",
            target="deploy",
            condition="tests pass",
        ),
        Edge(
            edge_type=EdgeType.CONDITIONAL,
            source="review",
            target="fix",
            condition="tests fail",
        ),
    )
    return _make_flow(name="conditional_flow", nodes=nodes, edges=edges)


def _fork_flow() -> Flow:
    """Fork-join flow: start -> fork(worker_a, worker_b) -> join -> done."""
    nodes = {
        "start": _make_node("start", "Initialize", NodeType.ENTRY),
        "worker_a": _make_node("worker_a", "Do task A"),
        "worker_b": _make_node("worker_b", "Do task B"),
        "merge": _make_node("merge", "Merge results"),
        "done": _make_node("done", "Finalize", NodeType.EXIT),
    }
    edges = (
        Edge(
            edge_type=EdgeType.FORK,
            source="start",
            fork_targets=("worker_a", "worker_b"),
        ),
        Edge(
            edge_type=EdgeType.JOIN,
            join_sources=("worker_a", "worker_b"),
            target="merge",
        ),
        Edge(edge_type=EdgeType.UNCONDITIONAL, source="merge", target="done"),
    )
    return _make_flow(name="fork_flow", nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Tests: serialize_flow_graph
# ---------------------------------------------------------------------------


class TestSerializeFlowGraph:
    def test_serialize_linear_flow(self) -> None:
        """Linear flow: all nodes listed with types, all edges with arrows."""
        flow = _linear_flow()
        result = serialize_flow_graph(flow)

        # Nodes section
        assert "## Nodes" in result
        assert "**start**" in result
        assert "entry" in result
        assert "**analyze**" in result
        assert "task" in result
        assert "**deploy**" in result
        assert "exit" in result
        assert "Initialize the project" in result
        assert "Analyze the codebase" in result
        assert "Deploy to production" in result

        # Edges section
        assert "## Edges" in result
        assert "start -> analyze" in result
        assert "analyze -> deploy" in result

    def test_serialize_conditional_flow(self) -> None:
        """Conditional flow: edges show conditions."""
        flow = _conditional_flow()
        result = serialize_flow_graph(flow)

        assert 'review -> deploy [condition: "tests pass"]' in result
        assert 'review -> fix [condition: "tests fail"]' in result

    def test_serialize_fork_flow(self) -> None:
        """Fork flow: fork targets and join sources listed."""
        flow = _fork_flow()
        result = serialize_flow_graph(flow)

        assert "start -> fork(worker_a, worker_b)" in result
        assert "join(worker_a, worker_b) -> merge" in result
        assert "merge -> done" in result

    def test_serialize_truncates_long_prompts(self) -> None:
        """Prompts longer than 120 chars are truncated with ellipsis."""
        long_prompt = "A" * 200
        nodes = {"task": _make_node("task", long_prompt)}
        flow = _make_flow(nodes=nodes)
        result = serialize_flow_graph(flow)

        # Should be truncated to 117 chars + "..."
        assert "..." in result
        assert long_prompt not in result

    def test_serialize_empty_edges(self) -> None:
        """Flow with nodes but no edges still shows nodes section."""
        nodes = {"solo": _make_node("solo", "Work alone")}
        flow = _make_flow(nodes=nodes, edges=())
        result = serialize_flow_graph(flow)

        assert "## Nodes" in result
        assert "**solo**" in result
        assert "## Edges" in result


# ---------------------------------------------------------------------------
# Tests: build_orchestrator_system_prompt
# ---------------------------------------------------------------------------


class TestBuildOrchestratorSystemPrompt:
    def test_contains_identity(self) -> None:
        """Prompt includes orchestrator identity statement."""
        flow = _linear_flow()
        prompt = build_orchestrator_system_prompt(flow, "/data/run-1", "/project")

        assert "Flowstate orchestrator agent" in prompt
        assert "linear_flow" in prompt

    def test_contains_flow_graph(self) -> None:
        """Prompt includes the serialized flow graph."""
        flow = _linear_flow()
        prompt = build_orchestrator_system_prompt(flow, "/data/run-1", "/project")

        assert "# Flow Graph" in prompt
        assert "## Nodes" in prompt
        assert "## Edges" in prompt
        assert "start -> analyze" in prompt

    def test_contains_task_execution_protocol(self) -> None:
        """Prompt describes the task execution protocol."""
        flow = _linear_flow()
        prompt = build_orchestrator_system_prompt(flow, "/data/run-1", "/project")

        assert "# Task Execution Protocol" in prompt
        assert "INPUT.md" in prompt
        assert "Agent tool" in prompt
        assert 'model: "opus"' in prompt
        assert "SUMMARY.md" in prompt

    def test_contains_judge_evaluation_protocol(self) -> None:
        """Prompt describes the judge evaluation protocol."""
        flow = _linear_flow()
        prompt = build_orchestrator_system_prompt(flow, "/data/run-1", "/project")

        assert "# Judge Evaluation Protocol" in prompt
        assert "REQUEST.md" in prompt
        assert "DECISION.json" in prompt

    def test_contains_decision_json_format(self) -> None:
        """Prompt includes DECISION.json format specification."""
        flow = _linear_flow()
        prompt = build_orchestrator_system_prompt(flow, "/data/run-1", "/project")

        assert "# DECISION.json Format" in prompt
        assert '"decision"' in prompt
        assert '"reasoning"' in prompt
        assert '"confidence"' in prompt
        assert "__none__" in prompt

    def test_contains_fork_handling(self) -> None:
        """Prompt includes fork handling instructions."""
        flow = _linear_flow()
        prompt = build_orchestrator_system_prompt(flow, "/data/run-1", "/project")

        assert "# Fork Handling" in prompt
        assert "parallel Agent tool calls" in prompt

    def test_contains_file_paths(self) -> None:
        """Prompt includes run_data_dir and cwd."""
        flow = _linear_flow()
        prompt = build_orchestrator_system_prompt(
            flow, "/home/user/.flowstate/runs/run-abc", "/workspace/myproject"
        )

        assert "/home/user/.flowstate/runs/run-abc" in prompt
        assert "/workspace/myproject" in prompt
        assert "tasks/<node_name>-<generation>/" in prompt
        assert "judge/<source_node>-<generation>/" in prompt

    def test_all_required_sections_present(self) -> None:
        """Verify all 7 required sections are in the prompt."""
        flow = _conditional_flow()
        prompt = build_orchestrator_system_prompt(flow, "/data/run-1", "/project")

        required_sections = [
            "# Flowstate Orchestrator Agent",
            "# Flow Graph",
            "# Task Execution Protocol",
            "# Judge Evaluation Protocol",
            "# DECISION.json Format",
            "# Fork Handling",
            "# File Paths",
        ]
        for section in required_sections:
            assert section in prompt, f"Missing section: {section}"
