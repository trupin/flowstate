"""Tests for the Flowstate DSL type checker (Flow AST -> validation errors).

Covers all four rule sets:
  - S1-S8: Structural rules (DSL-003)
  - E1-E9: Edge rules (DSL-004)
  - C1-C3: Cycle rules (DSL-005)
  - F1-F3: Fork-join rules (DSL-006)
"""

from pathlib import Path

from flowstate.dsl.ast import (
    ContextMode,
    Edge,
    EdgeConfig,
    EdgeType,
    ErrorPolicy,
    Flow,
    Node,
    NodeType,
)
from flowstate.dsl.exceptions import FlowTypeError
from flowstate.dsl.parser import parse_flow
from flowstate.dsl.type_checker import check_flow

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# Helper: build minimal valid flows for targeted rule testing
# ---------------------------------------------------------------------------


def _minimal_flow(
    nodes: dict[str, Node] | None = None,
    edges: tuple[Edge, ...] = (),
    workspace: str | None = "./test",
    budget_seconds: int = 3600,
    context: ContextMode = ContextMode.HANDOFF,
    **kwargs: object,
) -> Flow:
    """Build a minimal Flow for testing. If no nodes are given, creates entry->exit."""
    if nodes is None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
    if not edges:
        edges = (Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="end"),)
    return Flow(
        name="test_flow",
        budget_seconds=budget_seconds,
        on_error=ErrorPolicy.PAUSE,
        context=context,
        workspace=workspace,
        nodes=nodes,
        edges=edges,
        **kwargs,  # type: ignore[arg-type]
    )


def _errors_with_rule(errors: list[FlowTypeError], rule: str) -> list[FlowTypeError]:
    """Filter errors to those matching a given rule ID."""
    return [e for e in errors if e.rule == rule]


# ===========================================================================
# DSL-003: Structural rules S1-S8
# ===========================================================================


class TestS1ExactlyOneEntry:
    def test_no_entry(self) -> None:
        nodes = {
            "a": Node(name="a", node_type=NodeType.TASK, prompt="task"),
            "b": Node(name="b", node_type=NodeType.EXIT, prompt="exit"),
        }
        edges = (Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="b"),)
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        s1 = _errors_with_rule(errors, "S1")
        assert len(s1) == 1
        assert "none" in s1[0].message.lower()

    def test_multiple_entries(self) -> None:
        nodes = {
            "e1": Node(name="e1", node_type=NodeType.ENTRY, prompt="entry1"),
            "e2": Node(name="e2", node_type=NodeType.ENTRY, prompt="entry2"),
            "x": Node(name="x", node_type=NodeType.EXIT, prompt="exit"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="e1", target="x"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="e2", target="x"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        s1 = _errors_with_rule(errors, "S1")
        assert len(s1) == 1
        assert "2" in s1[0].message

    def test_valid_single_entry(self) -> None:
        flow = _minimal_flow()
        errors = check_flow(flow)
        s1 = _errors_with_rule(errors, "S1")
        assert len(s1) == 0


class TestS2AtLeastOneExit:
    def test_no_exit(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "task": Node(name="task", node_type=NodeType.TASK, prompt="do"),
        }
        edges = (Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="task"),)
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        s2 = _errors_with_rule(errors, "S2")
        assert len(s2) == 1

    def test_valid_with_exit(self) -> None:
        flow = _minimal_flow()
        errors = check_flow(flow)
        s2 = _errors_with_rule(errors, "S2")
        assert len(s2) == 0


class TestS3AllNodesReachable:
    def test_unreachable_node(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
            "orphan": Node(name="orphan", node_type=NodeType.TASK, prompt="lost"),
        }
        edges = (Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="end"),)
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        s3 = _errors_with_rule(errors, "S3")
        assert len(s3) == 1
        assert s3[0].location == "orphan"

    def test_all_reachable(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "mid": Node(name="mid", node_type=NodeType.TASK, prompt="work"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="mid"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="mid", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        s3 = _errors_with_rule(errors, "S3")
        assert len(s3) == 0


class TestS4EveryNodeCanReachExit:
    def test_dead_end_node(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="work a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="dead end"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="b"),
            # b has no edge to end; start->a->b is a dead end
            # But we also need an edge from start to end so the entry can reach exit
            Edge(edge_type=EdgeType.CONDITIONAL, source="a", target="end", condition="done"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        s4 = _errors_with_rule(errors, "S4")
        assert len(s4) == 1
        assert s4[0].location == "b"

    def test_all_can_reach_exit(self) -> None:
        flow = _minimal_flow()
        errors = check_flow(flow)
        s4 = _errors_with_rule(errors, "S4")
        assert len(s4) == 0


class TestS5NoDuplicateNames:
    """S5 is structurally prevented by using dict[str, Node]. This test
    verifies the dict deduplication works as expected.
    """

    def test_dict_prevents_duplicates(self) -> None:
        # Constructing a dict with the same key twice just overwrites
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        flow = _minimal_flow(nodes=nodes)
        errors = check_flow(flow)
        s5 = _errors_with_rule(errors, "S5")
        assert len(s5) == 0


class TestS6EntryNoIncoming:
    def test_entry_has_unconditional_incoming(self) -> None:
        """An unconditional edge targeting the entry node should trigger S6."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "mid": Node(name="mid", node_type=NodeType.TASK, prompt="work"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="mid"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="mid", target="end"),
            # Unconditional edge back to entry — violates S6
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="mid", target="start"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        s6 = _errors_with_rule(errors, "S6")
        assert len(s6) == 1
        assert s6[0].location == "start"

    def test_conditional_edge_to_entry_allowed(self) -> None:
        """A conditional edge targeting entry (cycle) should NOT trigger S6."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "mid": Node(name="mid", node_type=NodeType.TASK, prompt="work"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="mid"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="mid", target="end", condition="done"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="mid", target="start", condition="retry"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        s6 = _errors_with_rule(errors, "S6")
        assert len(s6) == 0

    def test_entry_no_incoming(self) -> None:
        flow = _minimal_flow()
        errors = check_flow(flow)
        s6 = _errors_with_rule(errors, "S6")
        assert len(s6) == 0


class TestS7ExitNoOutgoing:
    def test_exit_has_outgoing(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
            "extra": Node(name="extra", node_type=NodeType.TASK, prompt="extra"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="end", target="extra"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        s7 = _errors_with_rule(errors, "S7")
        assert len(s7) == 1
        assert s7[0].location == "end"

    def test_exit_no_outgoing(self) -> None:
        flow = _minimal_flow()
        errors = check_flow(flow)
        s7 = _errors_with_rule(errors, "S7")
        assert len(s7) == 0


class TestS8ResolvableCwd:
    def test_no_cwd_no_workspace(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="end"),)
        flow = _minimal_flow(nodes=nodes, edges=edges, workspace=None)
        errors = check_flow(flow)
        s8 = _errors_with_rule(errors, "S8")
        # Both start and end have no cwd and flow has no workspace
        assert len(s8) == 2

    def test_node_cwd_overrides_no_workspace(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin", cwd="./src"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done", cwd="./out"),
        }
        edges = (Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="end"),)
        flow = _minimal_flow(nodes=nodes, edges=edges, workspace=None)
        errors = check_flow(flow)
        s8 = _errors_with_rule(errors, "S8")
        assert len(s8) == 0

    def test_workspace_provides_default_cwd(self) -> None:
        flow = _minimal_flow(workspace="./project")
        errors = check_flow(flow)
        s8 = _errors_with_rule(errors, "S8")
        assert len(s8) == 0


class TestMultipleStructuralErrors:
    """Verify checker collects multiple errors (does not short-circuit)."""

    def test_s2_and_s8_together(self) -> None:
        # No exit + no workspace = S2 + S8 errors
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "mid": Node(name="mid", node_type=NodeType.TASK, prompt="work"),
        }
        edges = (Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="mid"),)
        flow = _minimal_flow(nodes=nodes, edges=edges, workspace=None)
        errors = check_flow(flow)
        rules = {e.rule for e in errors}
        assert "S2" in rules
        assert "S8" in rules


# ===========================================================================
# DSL-003: Positive tests — valid fixture flows produce no structural errors
# ===========================================================================


class TestValidFixtureFlows:
    """All Appendix A flows should produce no errors (structural or otherwise)."""

    def test_valid_linear(self) -> None:
        flow = parse_flow(load_fixture("valid_linear.flow"))
        errors = check_flow(flow)
        assert errors == []

    def test_valid_fork_join(self) -> None:
        flow = parse_flow(load_fixture("valid_fork_join.flow"))
        errors = check_flow(flow)
        assert errors == []

    def test_valid_cycle(self) -> None:
        flow = parse_flow(load_fixture("valid_cycle.flow"))
        errors = check_flow(flow)
        assert errors == []

    def test_valid_fork_join_cycle(self) -> None:
        flow = parse_flow(load_fixture("valid_fork_join_cycle.flow"))
        errors = check_flow(flow)
        assert errors == []

    def test_valid_scheduled_deploy(self) -> None:
        flow = parse_flow(load_fixture("valid_scheduled_deploy.flow"))
        errors = check_flow(flow)
        assert errors == []

    def test_valid_recurring_audit(self) -> None:
        flow = parse_flow(load_fixture("valid_recurring_audit.flow"))
        errors = check_flow(flow)
        assert errors == []


# ===========================================================================
# DSL-004: Edge rules E1-E9
# ===========================================================================


class TestE1SingleEdgeMustBeUnconditional:
    def test_single_conditional_is_error(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.CONDITIONAL, source="start", target="end", condition="always"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e1 = _errors_with_rule(errors, "E1")
        assert len(e1) == 1
        assert e1[0].location == "start"

    def test_single_unconditional_is_valid(self) -> None:
        flow = _minimal_flow()
        errors = check_flow(flow)
        e1 = _errors_with_rule(errors, "E1")
        assert len(e1) == 0

    def test_single_fork_is_valid(self) -> None:
        """A single fork edge (one Edge object, multiple targets) is valid."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "b")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e1 = _errors_with_rule(errors, "E1")
        assert len(e1) == 0


class TestE2MultipleEdgesAllConditionalOrSingleFork:
    def test_mixed_unconditional_and_conditional_default_edge(self) -> None:
        """1 unconditional + 1 conditional is a valid default-edge pattern (no E2)."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="start", target="b", condition="maybe"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="b", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e2 = _errors_with_rule(errors, "E2")
        assert len(e2) == 0

    def test_two_unconditional_triggers_e2(self) -> None:
        """2 unconditional edges (no conditional) should trigger E2."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="b"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="b", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e2 = _errors_with_rule(errors, "E2")
        assert len(e2) == 1
        assert e2[0].location == "start"

    def test_all_conditional_is_valid(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.CONDITIONAL, source="start", target="a", condition="path a"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="start", target="b", condition="path b"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="b", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e2 = _errors_with_rule(errors, "E2")
        assert len(e2) == 0


class TestE3NoMixingForkAndConditional:
    def test_fork_plus_conditional(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "c": Node(name="c", node_type=NodeType.TASK, prompt="c"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "b")),
            Edge(edge_type=EdgeType.CONDITIONAL, source="start", target="c", condition="alt"),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="c", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e3 = _errors_with_rule(errors, "E3")
        assert len(e3) == 1
        assert e3[0].location == "start"


class TestE4DanglingReferences:
    def test_nonexistent_source(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="ghost", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e4 = _errors_with_rule(errors, "E4")
        assert len(e4) >= 1
        assert any("ghost" in e.message for e in e4)

    def test_nonexistent_target(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="nowhere"),)
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e4 = _errors_with_rule(errors, "E4")
        assert len(e4) >= 1
        assert any("nowhere" in e.message for e in e4)

    def test_nonexistent_fork_target(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "nonexistent")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "nonexistent"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e4 = _errors_with_rule(errors, "E4")
        assert len(e4) >= 1
        assert any("nonexistent" in e.message for e in e4)


class TestE5ForkMustMatchJoin:
    def test_unmatched_fork(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "b")),
            # No matching join for [a, b]
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="b", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e5 = _errors_with_rule(errors, "E5")
        assert len(e5) == 1

    def test_matched_fork_join_valid(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "b")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e5 = _errors_with_rule(errors, "E5")
        assert len(e5) == 0


class TestE6JoinMustMatchFork:
    def test_unmatched_join(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="b"),
            # Join without matching fork
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e6 = _errors_with_rule(errors, "E6")
        assert len(e6) == 1


class TestE7SessionNotOnForkJoin:
    def test_session_on_fork_explicit(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(
                edge_type=EdgeType.FORK,
                source="start",
                fork_targets=("a", "b"),
                config=EdgeConfig(context=ContextMode.SESSION),
            ),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e7 = _errors_with_rule(errors, "E7")
        assert len(e7) >= 1

    def test_session_inherited_on_fork(self) -> None:
        """Flow-level context=session should trigger E7 on fork edges."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "b")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges, context=ContextMode.SESSION)
        errors = check_flow(flow)
        e7 = _errors_with_rule(errors, "E7")
        # Both fork and join should be flagged
        assert len(e7) == 2

    def test_handoff_on_fork_valid(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(
                edge_type=EdgeType.FORK,
                source="start",
                fork_targets=("a", "b"),
                config=EdgeConfig(context=ContextMode.HANDOFF),
            ),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e7 = _errors_with_rule(errors, "E7")
        assert len(e7) == 0


class TestE8DelayAndScheduleMutuallyExclusive:
    def test_both_delay_and_schedule(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(
                edge_type=EdgeType.UNCONDITIONAL,
                source="start",
                target="end",
                config=EdgeConfig(delay_seconds=300, schedule="0 * * * *"),
            ),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e8 = _errors_with_rule(errors, "E8")
        assert len(e8) == 1

    def test_only_delay_valid(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(
                edge_type=EdgeType.UNCONDITIONAL,
                source="start",
                target="end",
                config=EdgeConfig(delay_seconds=300),
            ),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e8 = _errors_with_rule(errors, "E8")
        assert len(e8) == 0

    def test_only_schedule_valid(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(
                edge_type=EdgeType.UNCONDITIONAL,
                source="start",
                target="end",
                config=EdgeConfig(schedule="0 2 * * *"),
            ),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e8 = _errors_with_rule(errors, "E8")
        assert len(e8) == 0


class TestE9ValidCronExpression:
    def test_invalid_cron(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(
                edge_type=EdgeType.UNCONDITIONAL,
                source="start",
                target="end",
                config=EdgeConfig(schedule="not a cron"),
            ),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e9 = _errors_with_rule(errors, "E9")
        assert len(e9) == 1
        assert "not a cron" in e9[0].message

    def test_valid_cron(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(
                edge_type=EdgeType.UNCONDITIONAL,
                source="start",
                target="end",
                config=EdgeConfig(schedule="0 2 * * *"),
            ),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e9 = _errors_with_rule(errors, "E9")
        assert len(e9) == 0


class TestEdgeRulesPositive:
    def test_valid_conditional_branching(self) -> None:
        """Two conditional edges from the same node should be valid."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="a", target="b", condition="path b"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="a", target="end", condition="done"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="b", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        edge_errors = [e for e in errors if e.rule.startswith("E")]
        assert edge_errors == []


# ===========================================================================
# DSL-005: Cycle rules C1-C3
# ===========================================================================


class TestC1CycleTargetOutsideForkGroup:
    def test_cycle_into_fork_group(self) -> None:
        """Cycling back to a node inside a fork-join group is forbidden."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="fork source"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="fork member b"),
            "c": Node(name="c", node_type=NodeType.TASK, prompt="fork member c"),
            "d": Node(name="d", node_type=NodeType.TASK, prompt="after join"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.FORK, source="a", fork_targets=("b", "c")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("b", "c"), target="d"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="d", target="end", condition="done"),
            # This cycle targets 'b', which is inside the fork group
            Edge(edge_type=EdgeType.CONDITIONAL, source="d", target="b", condition="retry"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        c1 = _errors_with_rule(errors, "C1")
        assert len(c1) == 1
        assert c1[0].location == "b"

    def test_cycle_to_fork_source_is_valid(self) -> None:
        """Cycling back to the fork source (not a member) is valid."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="fork source"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="fork member b"),
            "c": Node(name="c", node_type=NodeType.TASK, prompt="fork member c"),
            "d": Node(name="d", node_type=NodeType.TASK, prompt="after join"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.FORK, source="a", fork_targets=("b", "c")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("b", "c"), target="d"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="d", target="end", condition="done"),
            # Cycle targets 'a' (fork source), which is outside the group
            Edge(edge_type=EdgeType.CONDITIONAL, source="d", target="a", condition="retry"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        c1 = _errors_with_rule(errors, "C1")
        assert len(c1) == 0


class TestC2CycleMustHaveConditionalEdge:
    def test_unconditional_cycle(self) -> None:
        """A purely unconditional cycle (no conditional edges, no default-edge pattern)
        should trigger C2.

        The cycle is start -> a -> start. Both nodes have only 1 unconditional
        outgoing edge, so neither has a default-edge pattern. The cycle nodes may
        also trigger S4 (can't reach exit) but C2 must still fire.
        """
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="start"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges, budget_seconds=1800)
        errors = check_flow(flow)
        c2 = _errors_with_rule(errors, "C2")
        assert len(c2) >= 1

    def test_conditional_cycle_is_valid(self) -> None:
        """A cycle where the back-edge is conditional should not trigger C2."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "impl": Node(name="impl", node_type=NodeType.TASK, prompt="implement"),
            "verify": Node(name="verify", node_type=NodeType.TASK, prompt="verify"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="impl"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="impl", target="verify"),
            Edge(
                edge_type=EdgeType.CONDITIONAL, source="verify", target="end", condition="approved"
            ),
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="verify",
                target="impl",
                condition="needs work",
            ),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        c2 = _errors_with_rule(errors, "C2")
        assert len(c2) == 0

    def test_self_loop_conditional_is_valid(self) -> None:
        """A conditional self-loop should not trigger C2."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "check": Node(name="check", node_type=NodeType.TASK, prompt="check"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="check"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="check", target="end", condition="healthy"),
            Edge(
                edge_type=EdgeType.CONDITIONAL, source="check", target="check", condition="not yet"
            ),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        c2 = _errors_with_rule(errors, "C2")
        assert len(c2) == 0


class TestC3CyclesRequireBudget:
    def test_cycle_with_zero_budget(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="a", target="end", condition="done"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="a", target="a", condition="retry"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges, budget_seconds=0)
        errors = check_flow(flow)
        c3 = _errors_with_rule(errors, "C3")
        assert len(c3) == 1

    def test_cycle_with_positive_budget_valid(self) -> None:
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="a", target="end", condition="done"),
            Edge(edge_type=EdgeType.CONDITIONAL, source="a", target="a", condition="retry"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges, budget_seconds=3600)
        errors = check_flow(flow)
        c3 = _errors_with_rule(errors, "C3")
        assert len(c3) == 0


class TestCycleRulesAcyclicFlow:
    def test_acyclic_produces_no_cycle_errors(self) -> None:
        flow = _minimal_flow()
        errors = check_flow(flow)
        c_errors = [e for e in errors if e.rule.startswith("C")]
        assert c_errors == []


# ===========================================================================
# DSL-006: Fork-join rules F1-F3
# ===========================================================================


class TestF1NoPartialOverlap:
    def test_overlapping_fork_groups(self) -> None:
        """Two fork groups sharing member 'c' should trigger F1."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "c": Node(name="c", node_type=NodeType.TASK, prompt="c"),
            "d": Node(name="d", node_type=NodeType.TASK, prompt="d"),
            "j1": Node(name="j1", node_type=NodeType.TASK, prompt="j1"),
            "j2": Node(name="j2", node_type=NodeType.TASK, prompt="j2"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            # Fork 1: a -> [b, c]
            Edge(edge_type=EdgeType.FORK, source="a", fork_targets=("b", "c")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("b", "c"), target="j1"),
            # Fork 2: a -> [c, d] — c is in both groups
            Edge(edge_type=EdgeType.FORK, source="a", fork_targets=("c", "d")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("c", "d"), target="j2"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="j1", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="j2", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        f1 = _errors_with_rule(errors, "F1")
        assert len(f1) == 1
        assert "c" in f1[0].location

    def test_disjoint_fork_groups_valid(self) -> None:
        """Two sequential fork groups with disjoint members should pass."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "j1": Node(name="j1", node_type=NodeType.TASK, prompt="j1"),
            "c": Node(name="c", node_type=NodeType.TASK, prompt="c"),
            "d": Node(name="d", node_type=NodeType.TASK, prompt="d"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            # First fork group: start -> [a, b] -> j1
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "b")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="j1"),
            # Second fork group: j1 -> [c, d] -> end (disjoint from first)
            Edge(edge_type=EdgeType.FORK, source="j1", fork_targets=("c", "d")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("c", "d"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        f1 = _errors_with_rule(errors, "F1")
        assert len(f1) == 0

    def test_nested_fork_join_valid(self) -> None:
        """The spec example: nested fork-join with disjoint member sets."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "c": Node(name="c", node_type=NodeType.TASK, prompt="c"),
            "d": Node(name="d", node_type=NodeType.TASK, prompt="d"),
            "e": Node(name="e", node_type=NodeType.TASK, prompt="e"),
            "b_done": Node(name="b_done", node_type=NodeType.TASK, prompt="b_done"),
            "f": Node(name="f", node_type=NodeType.EXIT, prompt="f"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            # Outer fork: a -> [b, c]
            Edge(edge_type=EdgeType.FORK, source="a", fork_targets=("b", "c")),
            # Inner fork: b -> [d, e]
            Edge(edge_type=EdgeType.FORK, source="b", fork_targets=("d", "e")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("d", "e"), target="b_done"),
            # Outer join: [b_done, c] -> f
            Edge(edge_type=EdgeType.JOIN, join_sources=("b_done", "c"), target="f"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        f1 = _errors_with_rule(errors, "F1")
        assert len(f1) == 0


class TestF2JoinNodeNotForkSourceSameDeclaration:
    def test_combined_join_and_fork_in_single_edge(self) -> None:
        """A single Edge with both join_sources and fork_targets is invalid."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "d": Node(name="d", node_type=NodeType.TASK, prompt="d"),
            "e": Node(name="e", node_type=NodeType.TASK, prompt="e"),
            "f": Node(name="f", node_type=NodeType.TASK, prompt="f"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        # Programmatically construct an edge that is both join and fork
        malformed = Edge(
            edge_type=EdgeType.JOIN,  # or FORK - the point is both fields are set
            join_sources=("a", "b"),
            target="d",
            fork_targets=("e", "f"),
        )
        edges = (
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "b")),
            malformed,
            Edge(edge_type=EdgeType.JOIN, join_sources=("e", "f"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        f2 = _errors_with_rule(errors, "F2")
        assert len(f2) == 1

    def test_separate_join_then_fork_valid(self) -> None:
        """Separate join and fork declarations through the same node is valid."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "d": Node(name="d", node_type=NodeType.TASK, prompt="d"),
            "e": Node(name="e", node_type=NodeType.TASK, prompt="e"),
            "f": Node(name="f", node_type=NodeType.TASK, prompt="f"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "b")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="d"),
            # d is join target AND fork source, but in separate declarations
            Edge(edge_type=EdgeType.FORK, source="d", fork_targets=("e", "f")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("e", "f"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        f2 = _errors_with_rule(errors, "F2")
        assert len(f2) == 0


class TestF3ForkTargetsMustConverge:
    def test_valid_fork_join_convergence(self) -> None:
        """All fork targets converge to join — no F3 errors."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="begin"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            Edge(edge_type=EdgeType.FORK, source="start", fork_targets=("a", "b")),
            Edge(edge_type=EdgeType.JOIN, join_sources=("a", "b"), target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        f3 = _errors_with_rule(errors, "F3")
        assert len(f3) == 0


class TestForkJoinNoForkJoin:
    def test_no_forks_no_errors(self) -> None:
        """A flow with no fork-join edges should produce no F-rule errors."""
        flow = _minimal_flow()
        errors = check_flow(flow)
        f_errors = [e for e in errors if e.rule.startswith("F")]
        assert f_errors == []


class TestForkJoinAppendixFlows:
    def test_appendix_a2_fork_join(self) -> None:
        flow = parse_flow(load_fixture("valid_fork_join.flow"))
        errors = check_flow(flow)
        f_errors = [e for e in errors if e.rule.startswith("F")]
        assert f_errors == []

    def test_appendix_a4_fork_join_with_cycle(self) -> None:
        flow = parse_flow(load_fixture("valid_fork_join_cycle.flow"))
        errors = check_flow(flow)
        f_errors = [e for e in errors if e.rule.startswith("F")]
        assert f_errors == []


class TestCycleAppendixFlows:
    def test_appendix_a3_iterative_refactor(self) -> None:
        flow = parse_flow(load_fixture("valid_cycle.flow"))
        errors = check_flow(flow)
        c_errors = [e for e in errors if e.rule.startswith("C")]
        assert c_errors == []

    def test_appendix_a4_feature_development(self) -> None:
        flow = parse_flow(load_fixture("valid_fork_join_cycle.flow"))
        errors = check_flow(flow)
        c_errors = [e for e in errors if e.rule.startswith("C")]
        assert c_errors == []

    def test_appendix_a5_deploy_and_monitor(self) -> None:
        flow = parse_flow(load_fixture("valid_scheduled_deploy.flow"))
        errors = check_flow(flow)
        c_errors = [e for e in errors if e.rule.startswith("C")]
        assert c_errors == []


# ===========================================================================
# Default-edge pattern tests (1 unconditional + 1+ conditional)
# ===========================================================================


class TestDefaultEdgeE2:
    """E2 rule: default-edge pattern (1 unconditional + 1+ conditional) is valid."""

    def test_default_edge_valid_e2(self) -> None:
        """Node with 1 unconditional + 1 conditional should NOT trigger E2."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="entry"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="task a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="task b"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="exit"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            # Node 'a' has 1 unconditional (default) + 1 conditional
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="b"),
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="a",
                target="end",
                condition="all done",
            ),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="b", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e2 = _errors_with_rule(errors, "E2")
        assert len(e2) == 0

    def test_default_edge_multiple_conditionals_valid(self) -> None:
        """1 unconditional + 2+ conditional is valid (no E2)."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="entry"),
            "hub": Node(name="hub", node_type=NodeType.TASK, prompt="decision hub"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="path a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="path b"),
            "fallback": Node(name="fallback", node_type=NodeType.TASK, prompt="fallback"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="exit"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="hub"),
            # hub has 1 unconditional (default) + 2 conditional
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="hub", target="fallback"),
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="hub",
                target="a",
                condition="path a",
            ),
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="hub",
                target="b",
                condition="path b",
            ),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="b", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="fallback", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e2 = _errors_with_rule(errors, "E2")
        assert len(e2) == 0

    def test_two_unconditional_plus_conditional_invalid(self) -> None:
        """2 unconditional + 1 conditional should still trigger E2."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="entry"),
            "hub": Node(name="hub", node_type=NodeType.TASK, prompt="hub"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "b": Node(name="b", node_type=NodeType.TASK, prompt="b"),
            "c": Node(name="c", node_type=NodeType.TASK, prompt="c"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="exit"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="hub"),
            # hub has 2 unconditional + 1 conditional — invalid
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="hub", target="a"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="hub", target="b"),
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="hub",
                target="c",
                condition="cond",
            ),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="b", target="end"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="c", target="end"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges)
        errors = check_flow(flow)
        e2 = _errors_with_rule(errors, "E2")
        assert len(e2) == 1
        assert "hub" in e2[0].message


class TestDefaultEdgeS6:
    """S6 rule: unconditional back-edges to entry with default-edge pattern."""

    def test_default_edge_to_entry_s6(self) -> None:
        """Unconditional back-edge to entry when entry has default-edge pattern: no S6."""
        nodes = {
            "moderator": Node(name="moderator", node_type=NodeType.ENTRY, prompt="moderate"),
            "worker": Node(name="worker", node_type=NodeType.TASK, prompt="work"),
            "done": Node(name="done", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            # moderator has default-edge pattern: 1 unconditional + 1 conditional
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="moderator", target="worker"),
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="moderator",
                target="done",
                condition="finished",
            ),
            # unconditional back-edge to entry
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="worker", target="moderator"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges, budget_seconds=1800)
        errors = check_flow(flow)
        s6 = _errors_with_rule(errors, "S6")
        assert len(s6) == 0

    def test_unconditional_to_entry_no_default_s6(self) -> None:
        """Unconditional back-edge to entry without default-edge pattern: triggers S6."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="entry"),
            "worker": Node(name="worker", node_type=NodeType.TASK, prompt="work"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="exit"),
        }
        edges = (
            # start has only 1 unconditional outgoing edge (no default-edge pattern)
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="worker"),
            # unconditional back-edge to entry without default-edge pattern
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="worker", target="start"),
            # worker also goes to end (so exit is reachable)
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="worker",
                target="end",
                condition="done",
            ),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges, budget_seconds=1800)
        errors = check_flow(flow)
        s6 = _errors_with_rule(errors, "S6")
        assert len(s6) == 1
        assert "start" in s6[0].message


class TestDefaultEdgeC2:
    """C2 rule: cycles through nodes with default-edge pattern."""

    def test_cycle_with_default_edge_c2(self) -> None:
        """Cycle through a node with default-edge pattern should NOT trigger C2."""
        nodes = {
            "moderator": Node(name="moderator", node_type=NodeType.ENTRY, prompt="moderate"),
            "worker": Node(name="worker", node_type=NodeType.TASK, prompt="work"),
            "done": Node(name="done", node_type=NodeType.EXIT, prompt="done"),
        }
        edges = (
            # moderator has default-edge pattern (conditional checkpoint)
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="moderator", target="worker"),
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="moderator",
                target="done",
                condition="finished",
            ),
            # unconditional back-edge closes the cycle
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="worker", target="moderator"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges, budget_seconds=1800)
        errors = check_flow(flow)
        c2 = _errors_with_rule(errors, "C2")
        assert len(c2) == 0

    def test_cycle_no_conditional_no_default_c2(self) -> None:
        """Pure unconditional cycle (no default-edge node) should still trigger C2."""
        nodes = {
            "start": Node(name="start", node_type=NodeType.ENTRY, prompt="entry"),
            "a": Node(name="a", node_type=NodeType.TASK, prompt="a"),
            "end": Node(name="end", node_type=NodeType.EXIT, prompt="exit"),
        }
        edges = (
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="a"),
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="start"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges, budget_seconds=1800)
        errors = check_flow(flow)
        c2 = _errors_with_rule(errors, "C2")
        assert len(c2) >= 1


class TestDefaultEdgeFullScenario:
    """Full integration scenario for default-edge support."""

    def test_full_default_edge_scenario(self) -> None:
        """The full user scenario: moderator loop with default edge. Zero type errors."""
        nodes = {
            "moderator": Node(name="moderator", node_type=NodeType.ENTRY, prompt="moderate"),
            "alice": Node(name="alice", node_type=NodeType.TASK, prompt="alice works"),
            "bob": Node(name="bob", node_type=NodeType.TASK, prompt="bob works"),
            "done": Node(name="done", node_type=NodeType.EXIT, prompt="finished"),
        }
        edges = (
            # moderator -> alice (unconditional, the default)
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="moderator", target="alice"),
            # moderator -> done when "condition" (conditional exit)
            Edge(
                edge_type=EdgeType.CONDITIONAL,
                source="moderator",
                target="done",
                condition="condition",
            ),
            # alice -> bob (unconditional)
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="alice", target="bob"),
            # bob -> moderator (unconditional back-edge)
            Edge(edge_type=EdgeType.UNCONDITIONAL, source="bob", target="moderator"),
        )
        flow = _minimal_flow(nodes=nodes, edges=edges, budget_seconds=1800)
        errors = check_flow(flow)
        assert len(errors) == 0, f"Expected zero errors, got: {errors}"
