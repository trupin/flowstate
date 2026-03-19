import dataclasses

import pytest

from flowstate.dsl.ast import (
    ContextMode,
    Edge,
    EdgeConfig,
    EdgeType,
    ErrorPolicy,
    Flow,
    Node,
    NodeType,
    OverlapPolicy,
    Param,
    ParamType,
)


class TestEnumValues:
    def test_node_type_values(self) -> None:
        assert NodeType.ENTRY.value == "entry"
        assert NodeType.TASK.value == "task"
        assert NodeType.EXIT.value == "exit"

    def test_edge_type_values(self) -> None:
        assert EdgeType.UNCONDITIONAL.value == "unconditional"
        assert EdgeType.CONDITIONAL.value == "conditional"
        assert EdgeType.FORK.value == "fork"
        assert EdgeType.JOIN.value == "join"

    def test_context_mode_values(self) -> None:
        assert ContextMode.HANDOFF.value == "handoff"
        assert ContextMode.SESSION.value == "session"
        assert ContextMode.NONE.value == "none"

    def test_error_policy_values(self) -> None:
        assert ErrorPolicy.PAUSE.value == "pause"
        assert ErrorPolicy.ABORT.value == "abort"
        assert ErrorPolicy.SKIP.value == "skip"

    def test_param_type_values(self) -> None:
        assert ParamType.STRING.value == "string"
        assert ParamType.NUMBER.value == "number"
        assert ParamType.BOOL.value == "bool"

    def test_overlap_policy_values(self) -> None:
        assert OverlapPolicy.SKIP.value == "skip"
        assert OverlapPolicy.QUEUE.value == "queue"
        assert OverlapPolicy.PARALLEL.value == "parallel"


class TestEnumStringBehavior:
    def test_node_type_is_string(self) -> None:
        assert NodeType.ENTRY == "entry"
        assert isinstance(NodeType.ENTRY, str)

    def test_edge_type_is_string(self) -> None:
        assert EdgeType.FORK == "fork"
        assert isinstance(EdgeType.FORK, str)

    def test_context_mode_is_string(self) -> None:
        assert ContextMode.HANDOFF == "handoff"

    def test_error_policy_is_string(self) -> None:
        assert ErrorPolicy.PAUSE == "pause"

    def test_param_type_is_string(self) -> None:
        assert ParamType.STRING == "string"

    def test_overlap_policy_is_string(self) -> None:
        assert OverlapPolicy.SKIP == "skip"


class TestParam:
    def test_creation_with_default(self) -> None:
        p = Param(name="count", type=ParamType.NUMBER, default=10.0)
        assert p.name == "count"
        assert p.type == ParamType.NUMBER
        assert p.default == 10.0

    def test_creation_without_default(self) -> None:
        p = Param(name="name", type=ParamType.STRING)
        assert p.name == "name"
        assert p.type == ParamType.STRING
        assert p.default is None


class TestNode:
    def test_entry_node(self) -> None:
        n = Node(name="start", node_type=NodeType.ENTRY, prompt="Begin work")
        assert n.name == "start"
        assert n.node_type == NodeType.ENTRY
        assert n.prompt == "Begin work"
        assert n.cwd is None
        assert n.line == 0
        assert n.column == 0

    def test_task_node_with_cwd(self) -> None:
        n = Node(name="build", node_type=NodeType.TASK, prompt="Build it", cwd="/app")
        assert n.cwd == "/app"

    def test_exit_node_with_location(self) -> None:
        n = Node(name="done", node_type=NodeType.EXIT, prompt="Finish", line=10, column=5)
        assert n.line == 10
        assert n.column == 5


class TestEdgeConfig:
    def test_defaults(self) -> None:
        ec = EdgeConfig()
        assert ec.context is None
        assert ec.delay_seconds is None
        assert ec.schedule is None

    def test_with_values(self) -> None:
        ec = EdgeConfig(context=ContextMode.HANDOFF, delay_seconds=30)
        assert ec.context == ContextMode.HANDOFF
        assert ec.delay_seconds == 30


class TestEdge:
    def test_unconditional(self) -> None:
        e = Edge(edge_type=EdgeType.UNCONDITIONAL, source="a", target="b")
        assert e.source == "a"
        assert e.target == "b"
        assert e.fork_targets is None
        assert e.join_sources is None
        assert e.condition is None

    def test_conditional(self) -> None:
        e = Edge(edge_type=EdgeType.CONDITIONAL, source="a", target="b", condition="tests pass")
        assert e.condition == "tests pass"

    def test_fork(self) -> None:
        e = Edge(edge_type=EdgeType.FORK, source="a", fork_targets=("b", "c"))
        assert e.fork_targets == ("b", "c")
        assert e.target is None

    def test_join(self) -> None:
        e = Edge(edge_type=EdgeType.JOIN, join_sources=("b", "c"), target="d")
        assert e.join_sources == ("b", "c")

    def test_default_config(self) -> None:
        e = Edge(edge_type=EdgeType.UNCONDITIONAL)
        assert e.config == EdgeConfig()


class TestFlow:
    def test_minimal_creation(self) -> None:
        f = Flow(
            name="test",
            budget_seconds=3600,
            on_error=ErrorPolicy.PAUSE,
            context=ContextMode.HANDOFF,
        )
        assert f.name == "test"
        assert f.budget_seconds == 3600
        assert f.on_error == ErrorPolicy.PAUSE
        assert f.context == ContextMode.HANDOFF
        assert f.workspace is None
        assert f.schedule is None
        assert f.on_overlap == OverlapPolicy.SKIP
        assert f.params == ()
        assert f.nodes == {}
        assert f.edges == ()

    def test_full_creation(self) -> None:
        node = Node(name="start", node_type=NodeType.ENTRY, prompt="Go")
        edge = Edge(edge_type=EdgeType.UNCONDITIONAL, source="start", target="end")
        param = Param(name="x", type=ParamType.STRING)
        f = Flow(
            name="full",
            budget_seconds=7200,
            on_error=ErrorPolicy.ABORT,
            context=ContextMode.SESSION,
            workspace="/work",
            schedule="0 * * * *",
            on_overlap=OverlapPolicy.QUEUE,
            params=(param,),
            nodes={"start": node},
            edges=(edge,),
        )
        assert f.workspace == "/work"
        assert f.schedule == "0 * * * *"
        assert f.on_overlap == OverlapPolicy.QUEUE
        assert len(f.params) == 1
        assert len(f.nodes) == 1
        assert len(f.edges) == 1


class TestFrozenDataclasses:
    def test_param_frozen(self) -> None:
        p = Param(name="x", type=ParamType.STRING)
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.name = "y"  # type: ignore[misc]

    def test_node_frozen(self) -> None:
        n = Node(name="a", node_type=NodeType.TASK, prompt="do")
        with pytest.raises(dataclasses.FrozenInstanceError):
            n.name = "b"  # type: ignore[misc]

    def test_edge_config_frozen(self) -> None:
        ec = EdgeConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ec.context = ContextMode.HANDOFF  # type: ignore[misc]

    def test_edge_frozen(self) -> None:
        e = Edge(edge_type=EdgeType.UNCONDITIONAL)
        with pytest.raises(dataclasses.FrozenInstanceError):
            e.source = "a"  # type: ignore[misc]

    def test_flow_frozen(self) -> None:
        f = Flow(
            name="t", budget_seconds=100, on_error=ErrorPolicy.PAUSE, context=ContextMode.HANDOFF
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            f.name = "x"  # type: ignore[misc]


class TestDataclassReplace:
    def test_replace_node(self) -> None:
        original = Node(name="a", node_type=NodeType.TASK, prompt="do")
        modified = dataclasses.replace(original, name="b")
        assert original.name == "a"
        assert modified.name == "b"
        assert modified.node_type == NodeType.TASK


def test_all_types_importable() -> None:
    from flowstate.dsl.ast import (
        ContextMode,
        Edge,
        EdgeConfig,
        EdgeType,
        ErrorPolicy,
        Flow,
        Node,
        NodeType,
        OverlapPolicy,
        Param,
        ParamType,
    )

    assert all(
        cls is not None
        for cls in [
            Flow,
            Node,
            Edge,
            EdgeConfig,
            Param,
            NodeType,
            EdgeType,
            ContextMode,
            ErrorPolicy,
            ParamType,
            OverlapPolicy,
        ]
    )
