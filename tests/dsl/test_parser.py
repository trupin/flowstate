"""Tests for the Flowstate DSL parser (source -> AST)."""

from pathlib import Path

import pytest

from flowstate.dsl.ast import (
    ContextMode,
    EdgeConfig,
    EdgeType,
    ErrorPolicy,
    NodeType,
    OverlapPolicy,
    ParamType,
)
from flowstate.dsl.exceptions import FlowParseError
from flowstate.dsl.parser import parse_flow

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# 1. Appendix A.1 — Simple Linear Flow
# ---------------------------------------------------------------------------


class TestLinearFlow:
    @pytest.fixture()
    def flow(self):
        return parse_flow(load_fixture("valid_linear.flow"))

    def test_flow_name(self, flow):
        assert flow.name == "setup_project"

    def test_budget_seconds(self, flow):
        assert flow.budget_seconds == 1800  # 30m

    def test_on_error(self, flow):
        assert flow.on_error == ErrorPolicy.PAUSE

    def test_context(self, flow):
        assert flow.context == ContextMode.SESSION

    def test_workspace(self, flow):
        assert flow.workspace == "./new-project"

    def test_nodes(self, flow):
        assert set(flow.nodes.keys()) == {"scaffold", "add_ci", "done"}
        assert flow.nodes["scaffold"].node_type == NodeType.ENTRY
        assert flow.nodes["add_ci"].node_type == NodeType.TASK
        assert flow.nodes["done"].node_type == NodeType.EXIT

    def test_edges(self, flow):
        assert len(flow.edges) == 2
        assert all(e.edge_type == EdgeType.UNCONDITIONAL for e in flow.edges)
        assert flow.edges[0].source == "scaffold"
        assert flow.edges[0].target == "add_ci"
        assert flow.edges[1].source == "add_ci"
        assert flow.edges[1].target == "done"

    def test_no_params(self, flow):
        assert flow.params == ()


# ---------------------------------------------------------------------------
# 2. Appendix A.2 — Fork-Join Flow
# ---------------------------------------------------------------------------


class TestForkJoinFlow:
    @pytest.fixture()
    def flow(self):
        return parse_flow(load_fixture("valid_fork_join.flow"))

    def test_flow_name(self, flow):
        assert flow.name == "full_test"

    def test_budget_seconds(self, flow):
        assert flow.budget_seconds == 3600  # 1h

    def test_five_nodes(self, flow):
        assert len(flow.nodes) == 5

    def test_fork_edge(self, flow):
        fork_edges = [e for e in flow.edges if e.edge_type == EdgeType.FORK]
        assert len(fork_edges) == 1
        fe = fork_edges[0]
        assert fe.source == "analyze"
        assert set(fe.fork_targets or []) == {"test_unit", "test_integration", "test_e2e"}

    def test_join_edge(self, flow):
        join_edges = [e for e in flow.edges if e.edge_type == EdgeType.JOIN]
        assert len(join_edges) == 1
        je = join_edges[0]
        assert je.target == "report"
        assert set(je.join_sources or []) == {"test_unit", "test_integration", "test_e2e"}


# ---------------------------------------------------------------------------
# 3. Appendix A.3 — Cycle Flow
# ---------------------------------------------------------------------------


class TestCycleFlow:
    @pytest.fixture()
    def flow(self):
        return parse_flow(load_fixture("valid_cycle.flow"))

    def test_flow_name(self, flow):
        assert flow.name == "iterative_refactor"

    def test_param(self, flow):
        assert len(flow.params) == 1
        p = flow.params[0]
        assert p.name == "target"
        assert p.type == ParamType.STRING
        assert p.default is None

    def test_four_nodes(self, flow):
        assert len(flow.nodes) == 4

    def test_edge_counts(self, flow):
        assert len(flow.edges) == 4
        unconditional = [e for e in flow.edges if e.edge_type == EdgeType.UNCONDITIONAL]
        conditional = [e for e in flow.edges if e.edge_type == EdgeType.CONDITIONAL]
        assert len(unconditional) == 2
        assert len(conditional) == 2

    def test_conditional_edges_have_conditions(self, flow):
        conditional = [e for e in flow.edges if e.edge_type == EdgeType.CONDITIONAL]
        for e in conditional:
            assert e.condition is not None
            assert len(e.condition) > 0


# ---------------------------------------------------------------------------
# 4. Appendix A.4 — Fork-Join with Cycle (complex)
# ---------------------------------------------------------------------------


class TestComplexFlow:
    @pytest.fixture()
    def flow(self):
        return parse_flow(load_fixture("valid_fork_join_cycle.flow"))

    def test_flow_name(self, flow):
        assert flow.name == "feature_development"

    def test_no_workspace(self, flow):
        assert flow.workspace is None

    def test_all_nodes_have_cwd(self, flow):
        for name, node in flow.nodes.items():
            assert node.cwd is not None, f"node {name} missing cwd"

    def test_param(self, flow):
        assert len(flow.params) == 1
        assert flow.params[0].name == "feature"
        assert flow.params[0].type == ParamType.STRING

    def test_fork_and_join_present(self, flow):
        fork_edges = [e for e in flow.edges if e.edge_type == EdgeType.FORK]
        join_edges = [e for e in flow.edges if e.edge_type == EdgeType.JOIN]
        assert len(fork_edges) == 1
        assert len(join_edges) == 1

    def test_conditional_edges_present(self, flow):
        cond_edges = [e for e in flow.edges if e.edge_type == EdgeType.CONDITIONAL]
        assert len(cond_edges) == 2
        targets = {e.target for e in cond_edges}
        assert targets == {"ship", "design"}


# ---------------------------------------------------------------------------
# 5. Appendix A.5 — Scheduled Deployment
# ---------------------------------------------------------------------------


class TestScheduledFlow:
    @pytest.fixture()
    def flow(self):
        return parse_flow(load_fixture("valid_scheduled_deploy.flow"))

    def test_prepare_to_deploy_schedule(self, flow):
        edge = next(e for e in flow.edges if e.source == "prepare" and e.target == "deploy")
        assert edge.config.schedule == "0 2 * * *"

    def test_deploy_to_check_health_delay(self, flow):
        edge = next(e for e in flow.edges if e.source == "deploy" and e.target == "check_health")
        assert edge.config.delay_seconds == 300  # 5m

    def test_self_loop_delay(self, flow):
        edge = next(
            e for e in flow.edges if e.source == "check_health" and e.target == "check_health"
        )
        assert edge.config.delay_seconds == 120  # 2m

    def test_conditional_edges_on_check_health(self, flow):
        cond = [
            e
            for e in flow.edges
            if e.edge_type == EdgeType.CONDITIONAL and e.source == "check_health"
        ]
        assert len(cond) == 2


# ---------------------------------------------------------------------------
# 6. Appendix A.6 — Recurring Weekly Audit
# ---------------------------------------------------------------------------


class TestRecurringFlow:
    @pytest.fixture()
    def flow(self):
        return parse_flow(load_fixture("valid_recurring_audit.flow"))

    def test_schedule(self, flow):
        assert flow.schedule == "0 9 * * MON"

    def test_on_overlap(self, flow):
        assert flow.on_overlap == OverlapPolicy.SKIP


# ---------------------------------------------------------------------------
# 7. Syntax error — missing arrow
# ---------------------------------------------------------------------------


def test_syntax_error_missing_arrow():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "x" } task b { prompt = "y" } a b }'
    )
    with pytest.raises(FlowParseError) as exc_info:
        parse_flow(source)
    assert exc_info.value.line is not None


# ---------------------------------------------------------------------------
# 8. Syntax error — unclosed brace
# ---------------------------------------------------------------------------


def test_syntax_error_unclosed_brace():
    source = 'flow f { budget = 1h on_error = pause context = handoff entry a { prompt = "x" }'
    with pytest.raises(FlowParseError):
        parse_flow(source)


# ---------------------------------------------------------------------------
# 9. Syntax error — invalid keyword
# ---------------------------------------------------------------------------


def test_syntax_error_invalid_keyword():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'invalid_keyword = value entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    with pytest.raises(FlowParseError):
        parse_flow(source)


# ---------------------------------------------------------------------------
# 10. Edge config parsing
# ---------------------------------------------------------------------------


def test_edge_config_context():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } '
        "a -> b { context = session } }"
    )
    flow = parse_flow(source)
    assert flow.edges[0].config.context == ContextMode.SESSION


def test_edge_config_empty_block():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } '
        "a -> b {} }"
    )
    flow = parse_flow(source)
    assert flow.edges[0].config == EdgeConfig()


# ---------------------------------------------------------------------------
# 11. Parameter with default
# ---------------------------------------------------------------------------


def test_param_with_number_default():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        "param retries: number = 3 "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    p = flow.params[0]
    assert p.name == "retries"
    assert p.type == ParamType.NUMBER
    assert p.default == 3


def test_param_with_bool_default():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        "param verbose: bool = true "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    p = flow.params[0]
    assert p.name == "verbose"
    assert p.type == ParamType.BOOL
    assert p.default is True


def test_param_with_string_default():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'param label: string = "hello" '
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    p = flow.params[0]
    assert p.name == "label"
    assert p.type == ParamType.STRING
    assert p.default == "hello"


# ---------------------------------------------------------------------------
# 12. Parameter without default
# ---------------------------------------------------------------------------


def test_param_without_default():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        "param name: string "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    p = flow.params[0]
    assert p.name == "name"
    assert p.type == ParamType.STRING
    assert p.default is None


# ---------------------------------------------------------------------------
# 13. DURATION conversion
# ---------------------------------------------------------------------------


class TestDurationConversion:
    def test_seconds(self):
        source = (
            "flow f { budget = 30s on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        assert parse_flow(source).budget_seconds == 30

    def test_minutes(self):
        source = (
            "flow f { budget = 5m on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        assert parse_flow(source).budget_seconds == 300

    def test_hours(self):
        source = (
            "flow f { budget = 2h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        assert parse_flow(source).budget_seconds == 7200

    def test_zero_seconds(self):
        source = (
            "flow f { budget = 0s on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        assert parse_flow(source).budget_seconds == 0


# ---------------------------------------------------------------------------
# 14. String escaping / template variables
# ---------------------------------------------------------------------------


def test_template_variables_preserved():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        "param target: string "
        'entry a { prompt = "Analyze {{target}} now" } '
        'exit b { prompt = "done" } a -> b }'
    )
    flow = parse_flow(source)
    assert "{{target}}" in flow.nodes["a"].prompt


def test_template_variables_in_long_string():
    source = '''flow f {
    budget = 1h
    on_error = pause
    context = handoff
    param target: string
    entry a {
        prompt = """
        Analyze {{target}} and create a plan.
        """
    }
    exit b { prompt = "done" }
    a -> b
}'''
    flow = parse_flow(source)
    assert "{{target}}" in flow.nodes["a"].prompt


# ---------------------------------------------------------------------------
# 15. Line/column info
# ---------------------------------------------------------------------------


def test_line_column_on_nodes():
    source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a {
        prompt = "x"
    }
    task b {
        prompt = "y"
    }
    exit c {
        prompt = "z"
    }
    a -> b
    b -> c
}"""
    flow = parse_flow(source)
    # Entry node starts on line 5
    assert flow.nodes["a"].line > 0
    assert flow.nodes["b"].line > flow.nodes["a"].line
    assert flow.nodes["c"].line > flow.nodes["b"].line


def test_line_column_on_edges():
    source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a {
        prompt = "x"
    }
    exit b {
        prompt = "y"
    }
    a -> b
}"""
    flow = parse_flow(source)
    assert flow.edges[0].line > 0


# ---------------------------------------------------------------------------
# 16. Missing required attribute
# ---------------------------------------------------------------------------


def test_missing_budget():
    source = (
        "flow f { on_error = pause context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    with pytest.raises(FlowParseError, match="budget"):
        parse_flow(source)


def test_missing_on_error():
    source = (
        "flow f { budget = 1h context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    with pytest.raises(FlowParseError, match="on_error"):
        parse_flow(source)


def test_missing_context():
    source = (
        "flow f { budget = 1h on_error = pause "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    with pytest.raises(FlowParseError, match="context"):
        parse_flow(source)


# ---------------------------------------------------------------------------
# Additional: nodes dict keyed by name
# ---------------------------------------------------------------------------


def test_nodes_dict_keyed_by_name():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry analyze { prompt = "a" } exit done { prompt = "d" } '
        "analyze -> done }"
    )
    flow = parse_flow(source)
    assert "analyze" in flow.nodes
    assert "done" in flow.nodes
    assert flow.nodes["analyze"].name == "analyze"


# ---------------------------------------------------------------------------
# Additional: flow-level defaults
# ---------------------------------------------------------------------------


def test_default_on_overlap():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    assert flow.on_overlap == OverlapPolicy.SKIP


def test_no_schedule_is_none():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    assert flow.schedule is None


def test_no_workspace_is_none():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    assert flow.workspace is None


# ---------------------------------------------------------------------------
# Additional: edge config default
# ---------------------------------------------------------------------------


def test_edge_default_config():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    config = flow.edges[0].config
    assert config.context is None
    assert config.delay_seconds is None
    assert config.schedule is None


# ---------------------------------------------------------------------------
# Additional: empty prompt
# ---------------------------------------------------------------------------


def test_empty_prompt():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "" } exit b { prompt = "" } a -> b }'
    )
    flow = parse_flow(source)
    assert flow.nodes["a"].prompt == ""
    assert flow.nodes["b"].prompt == ""


# ---------------------------------------------------------------------------
# Additional: flow with only entry and exit
# ---------------------------------------------------------------------------


def test_minimal_flow():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "start" } exit b { prompt = "end" } a -> b }'
    )
    flow = parse_flow(source)
    assert len(flow.nodes) == 2
    assert len(flow.edges) == 1


# ---------------------------------------------------------------------------
# Additional: edge with delay duration conversion
# ---------------------------------------------------------------------------


def test_edge_delay_duration():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } '
        "a -> b { delay = 30s } }"
    )
    flow = parse_flow(source)
    assert flow.edges[0].config.delay_seconds == 30


def test_param_with_float_default():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        "param ratio: number = 3.14 "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    assert flow.params[0].default == 3.14


# ---------------------------------------------------------------------------
# Additional: on_error policies
# ---------------------------------------------------------------------------


def test_on_error_abort():
    source = (
        "flow f { budget = 1h on_error = abort context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    assert parse_flow(source).on_error == ErrorPolicy.ABORT


def test_on_error_skip():
    source = (
        "flow f { budget = 1h on_error = skip context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    assert parse_flow(source).on_error == ErrorPolicy.SKIP


# ---------------------------------------------------------------------------
# Additional: context modes
# ---------------------------------------------------------------------------


def test_context_none():
    source = (
        "flow f { budget = 1h on_error = pause context = none "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    assert parse_flow(source).context == ContextMode.NONE


def test_context_handoff():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    assert parse_flow(source).context == ContextMode.HANDOFF


# ---------------------------------------------------------------------------
# Additional: node with cwd
# ---------------------------------------------------------------------------


def test_node_cwd():
    source = (
        "flow f { budget = 1h on_error = pause context = handoff "
        'entry a { cwd = "./src" prompt = "x" } exit b { prompt = "y" } a -> b }'
    )
    flow = parse_flow(source)
    assert flow.nodes["a"].cwd == "./src"
    assert flow.nodes["b"].cwd is None


# ---------------------------------------------------------------------------
# Additional: prompts in nodes (triple-quoted)
# ---------------------------------------------------------------------------


def test_triple_quoted_prompt():
    source = '''flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a {
        prompt = """
        This is a long prompt
        spanning multiple lines.
        """
    }
    exit b { prompt = "done" }
    a -> b
}'''
    flow = parse_flow(source)
    prompt = flow.nodes["a"].prompt
    assert "long prompt" in prompt
    assert "multiple lines" in prompt


# ---------------------------------------------------------------------------
# Additional: judge parameter
# ---------------------------------------------------------------------------


class TestJudgeParameter:
    """Test judge boolean parameter at flow level and node level."""

    def test_flow_judge_true(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff judge = true "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.judge is True

    def test_flow_judge_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff judge = false "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.judge is False

    def test_flow_judge_default(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.judge is False

    def test_node_judge_true(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" judge = true } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].judge is True

    def test_node_judge_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" judge = false } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].judge is False

    def test_node_judge_default_is_none(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].judge is None
        assert flow.nodes["b"].judge is None

    def test_flow_and_node_judge_together(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff judge = true "
            'entry a { prompt = "x" judge = false } '
            'task b { prompt = "y" } '
            'exit c { prompt = "z" } a -> b b -> c }'
        )
        flow = parse_flow(source)
        assert flow.judge is True
        assert flow.nodes["a"].judge is False
        assert flow.nodes["b"].judge is None  # inherits from flow
        assert flow.nodes["c"].judge is None
