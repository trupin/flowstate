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

    def test_has_default_input(self, flow):
        """Linear flow fixture has a default input field (required by S9)."""
        assert len(flow.input_fields) >= 1
        assert flow.output_fields == ()


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

    def test_input_fields(self, flow):
        assert len(flow.input_fields) == 1
        f = flow.input_fields[0]
        assert f.name == "target"
        assert f.type == "string"
        assert f.default is None

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

    def test_input_fields(self, flow):
        assert len(flow.input_fields) == 1
        assert flow.input_fields[0].name == "feature"
        assert flow.input_fields[0].type == "string"

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
# 11. Input fields with defaults
# ---------------------------------------------------------------------------


def test_input_field_with_number_default():
    source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        retries: number = 3
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
    flow = parse_flow(source)
    f = flow.input_fields[0]
    assert f.name == "retries"
    assert f.type == "number"
    assert f.default == 3


def test_input_field_with_bool_default():
    source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        verbose: bool = true
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
    flow = parse_flow(source)
    f = flow.input_fields[0]
    assert f.name == "verbose"
    assert f.type == "bool"
    assert f.default is True


def test_input_field_with_string_default():
    source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        label: string = "hello"
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
    flow = parse_flow(source)
    f = flow.input_fields[0]
    assert f.name == "label"
    assert f.type == "string"
    assert f.default == "hello"


# ---------------------------------------------------------------------------
# 12. Input field without default
# ---------------------------------------------------------------------------


def test_input_field_without_default():
    source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        name: string
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
    flow = parse_flow(source)
    f = flow.input_fields[0]
    assert f.name == "name"
    assert f.type == "string"
    assert f.default is None


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
    source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        target: string
    }
    entry a { prompt = "Analyze {{target}} now" }
    exit b { prompt = "done" }
    a -> b
}"""
    flow = parse_flow(source)
    assert "{{target}}" in flow.nodes["a"].prompt


def test_template_variables_in_long_string():
    source = '''flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        target: string
    }
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


def test_input_field_with_float_default():
    source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        ratio: number = 3.14
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
    flow = parse_flow(source)
    assert flow.input_fields[0].default == 3.14


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


# ---------------------------------------------------------------------------
# Additional: worktree parameter
# ---------------------------------------------------------------------------


class TestWorktreeParameter:
    """Test worktree boolean parameter at flow level."""

    def test_flow_worktree_true(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff worktree = true "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.worktree is True

    def test_flow_worktree_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff worktree = false "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.worktree is False

    def test_flow_worktree_default_is_true(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.worktree is True


# ---------------------------------------------------------------------------
# Flow-level input/output blocks
# ---------------------------------------------------------------------------


class TestFlowInputOutput:
    """Test flow-level input and output block parsing."""

    def test_input_output_blocks(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        title: string
    }
    output {
        result: string
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
        flow = parse_flow(source)
        assert len(flow.input_fields) == 1
        assert flow.input_fields[0].name == "title"
        assert flow.input_fields[0].type == "string"
        assert flow.input_fields[0].default is None
        assert len(flow.output_fields) == 1
        assert flow.output_fields[0].name == "result"
        assert flow.output_fields[0].type == "string"

    def test_input_with_defaults(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        branch: string = "main"
        retries: number = 3
        verbose: bool = true
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
        flow = parse_flow(source)
        fields = flow.input_fields
        assert len(fields) == 3
        assert fields[0].name == "branch"
        assert fields[0].type == "string"
        assert fields[0].default == "main"
        assert fields[1].name == "retries"
        assert fields[1].type == "number"
        assert fields[1].default == 3
        assert fields[2].name == "verbose"
        assert fields[2].type == "bool"
        assert fields[2].default is True

    def test_multiple_fields(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        title: string
        priority: number
    }
    output {
        result: string
        score: number
        passed: bool
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
        flow = parse_flow(source)
        assert len(flow.input_fields) == 2
        assert len(flow.output_fields) == 3
        assert flow.output_fields[2].name == "passed"
        assert flow.output_fields[2].type == "bool"

    def test_input_only(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        title: string
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
        flow = parse_flow(source)
        assert len(flow.input_fields) == 1
        assert len(flow.output_fields) == 0

    def test_output_only(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    output {
        result: string
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
        flow = parse_flow(source)
        assert len(flow.input_fields) == 0
        assert len(flow.output_fields) == 1

    def test_no_input_output_defaults_empty(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.input_fields == ()
        assert flow.output_fields == ()

    def test_input_with_float_default(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        ratio: number = 3.14
    }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
        flow = parse_flow(source)
        assert flow.input_fields[0].default == 3.14

    def test_input_output_coexist_with_task_node(self):
        """Input/output blocks and task nodes should both parse correctly."""
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input {
        title: string
    }
    entry a { prompt = "start" }
    task b { prompt = "do work" }
    exit c { prompt = "done" }
    a -> b
    b -> c
}"""
        flow = parse_flow(source)
        assert len(flow.input_fields) == 1
        assert "b" in flow.nodes
        assert flow.nodes["b"].node_type == NodeType.TASK


# ---------------------------------------------------------------------------
# File and Await edge types
# ---------------------------------------------------------------------------


class TestFileEdges:
    """Test file edge type parsing."""

    def test_file_edge(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } '
            "a files bugfix a -> b }"
        )
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1
        assert file_edges[0].source == "a"
        assert file_edges[0].target == "bugfix"
        assert file_edges[0].condition is None

    def test_file_edge_conditional(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } '
            'a files bugfix when "bugs found" a -> b }'
        )
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1
        assert file_edges[0].source == "a"
        assert file_edges[0].target == "bugfix"
        assert file_edges[0].condition == "bugs found"

    def test_file_edge_has_line_info(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a files bugfix
    a -> b
}"""
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1
        assert file_edges[0].line > 0


class TestFileEdgeTimingVariants:
    """DSL-010: file edge timing variants (after/at)."""

    def test_file_delayed_edge(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry review { prompt = "x" } exit b { prompt = "y" } '
            "review files bugfix after 30m review -> b }"
        )
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1
        assert file_edges[0].source == "review"
        assert file_edges[0].target == "bugfix"
        assert file_edges[0].config.delay_seconds == 1800
        assert file_edges[0].condition is None

    def test_file_delayed_edge_seconds(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } '
            "a files child after 45s a -> b }"
        )
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1
        assert file_edges[0].config.delay_seconds == 45

    def test_file_delayed_edge_hours(self):
        source = (
            "flow f { budget = 2h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } '
            "a files child after 1h a -> b }"
        )
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1
        assert file_edges[0].config.delay_seconds == 3600

    def test_file_scheduled_edge(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry review { prompt = "x" } exit b { prompt = "y" } '
            'review files nightly at "0 2 * * *" review -> b }'
        )
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1
        assert file_edges[0].source == "review"
        assert file_edges[0].target == "nightly"
        assert file_edges[0].config.schedule == "0 2 * * *"
        assert file_edges[0].condition is None

    def test_file_delayed_edge_has_line_info(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a files child after 10m
    a -> b
}"""
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1
        assert file_edges[0].line > 0

    def test_file_scheduled_edge_has_line_info(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a files child at "0 3 * * *"
    a -> b
}"""
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1
        assert file_edges[0].line > 0

    def test_file_timing_coexists_with_regular_edges(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry review { prompt = "review code" }
    task fix { prompt = "fix issues" }
    exit done { prompt = "completed" }
    review files bugfix after 30m
    review files nightly at "0 2 * * *"
    review -> fix
    fix -> done
}"""
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        regular_edges = [
            e for e in flow.edges if e.edge_type in (EdgeType.UNCONDITIONAL, EdgeType.CONDITIONAL)
        ]
        assert len(file_edges) == 2
        assert len(regular_edges) == 2

        delayed = [e for e in file_edges if e.config.delay_seconds is not None]
        scheduled = [e for e in file_edges if e.config.schedule is not None]
        assert len(delayed) == 1
        assert delayed[0].config.delay_seconds == 1800
        assert len(scheduled) == 1
        assert scheduled[0].config.schedule == "0 2 * * *"

    def test_file_timing_with_plain_file_edge(self):
        """All three file edge forms coexist: plain, delayed, and scheduled."""
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } '
            "a files child1 "
            "a files child2 after 5m "
            'a files child3 at "0 0 * * *" '
            "a -> b }"
        )
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 3

        plain = [e for e in file_edges if e.target == "child1"]
        delayed = [e for e in file_edges if e.target == "child2"]
        scheduled = [e for e in file_edges if e.target == "child3"]

        assert len(plain) == 1
        assert plain[0].config.delay_seconds is None
        assert plain[0].config.schedule is None

        assert len(delayed) == 1
        assert delayed[0].config.delay_seconds == 300

        assert len(scheduled) == 1
        assert scheduled[0].config.schedule == "0 0 * * *"


class TestAwaitEdges:
    """Test await edge type parsing."""

    def test_await_edge(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } '
            "a awaits qa_check a -> b }"
        )
        flow = parse_flow(source)
        await_edges = [e for e in flow.edges if e.edge_type == EdgeType.AWAIT]
        assert len(await_edges) == 1
        assert await_edges[0].source == "a"
        assert await_edges[0].target == "qa_check"
        assert await_edges[0].condition is None

    def test_await_edge_conditional(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } '
            'a awaits qa_check when "needs qa" a -> b }'
        )
        flow = parse_flow(source)
        await_edges = [e for e in flow.edges if e.edge_type == EdgeType.AWAIT]
        assert len(await_edges) == 1
        assert await_edges[0].source == "a"
        assert await_edges[0].target == "qa_check"
        assert await_edges[0].condition == "needs qa"

    def test_await_edge_has_line_info(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a awaits qa_check
    a -> b
}"""
        flow = parse_flow(source)
        await_edges = [e for e in flow.edges if e.edge_type == EdgeType.AWAIT]
        assert len(await_edges) == 1
        assert await_edges[0].line > 0


class TestFileAwaitEdgeMixed:
    """Test mixing file/await edges with regular edges."""

    def test_file_and_await_together(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry review { prompt = "review code" }
    task fix { prompt = "fix issues" }
    exit done { prompt = "completed" }
    review files bugfix
    review awaits qa_check
    review -> fix
    fix -> done
}"""
        flow = parse_flow(source)
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        await_edges = [e for e in flow.edges if e.edge_type == EdgeType.AWAIT]
        regular_edges = [
            e for e in flow.edges if e.edge_type in (EdgeType.UNCONDITIONAL, EdgeType.CONDITIONAL)
        ]
        assert len(file_edges) == 1
        assert len(await_edges) == 1
        assert len(regular_edges) == 2

    def test_file_and_input_output_together(self):
        """Input/output blocks and file edges coexist in the same flow."""
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    input { title: string }
    output { result: string }
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a files other_flow
    a -> b
}"""
        flow = parse_flow(source)
        assert len(flow.input_fields) == 1
        assert len(flow.output_fields) == 1
        file_edges = [e for e in flow.edges if e.edge_type == EdgeType.FILE]
        assert len(file_edges) == 1


# ---------------------------------------------------------------------------
# DSL-009: Wait, Fence, Atomic node types + max_parallel
# ---------------------------------------------------------------------------


class TestWaitNode:
    """Test parsing of wait nodes."""

    def test_wait_node_with_delay(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    wait cooldown { delay = 1h }
    exit b { prompt = "done" }
    a -> cooldown
    cooldown -> b
}"""
        flow = parse_flow(source)
        assert "cooldown" in flow.nodes
        node = flow.nodes["cooldown"]
        assert node.node_type == NodeType.WAIT
        assert node.prompt == ""
        assert node.wait_delay_seconds == 3600
        assert node.wait_until_cron is None

    def test_wait_node_with_delay_minutes(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    wait pause { delay = 30m }
    exit b { prompt = "done" }
    a -> pause
    pause -> b
}"""
        flow = parse_flow(source)
        node = flow.nodes["pause"]
        assert node.wait_delay_seconds == 1800

    def test_wait_node_with_until(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    wait market_open { until = "0 9 * * 1-5" }
    exit b { prompt = "done" }
    a -> market_open
    market_open -> b
}"""
        flow = parse_flow(source)
        node = flow.nodes["market_open"]
        assert node.node_type == NodeType.WAIT
        assert node.prompt == ""
        assert node.wait_delay_seconds is None
        assert node.wait_until_cron == "0 9 * * 1-5"

    def test_wait_node_has_line_info(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    wait w { delay = 30s }
    exit b { prompt = "done" }
    a -> w
    w -> b
}"""
        flow = parse_flow(source)
        assert flow.nodes["w"].line > 0


class TestFenceNode:
    """Test parsing of fence nodes."""

    def test_fence_node(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    fence sync_point { }
    exit b { prompt = "done" }
    a -> sync_point
    sync_point -> b
}"""
        flow = parse_flow(source)
        assert "sync_point" in flow.nodes
        node = flow.nodes["sync_point"]
        assert node.node_type == NodeType.FENCE
        assert node.prompt == ""

    def test_fence_node_has_line_info(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    fence barrier { }
    exit b { prompt = "done" }
    a -> barrier
    barrier -> b
}"""
        flow = parse_flow(source)
        assert flow.nodes["barrier"].line > 0


class TestAtomicNode:
    """Test parsing of atomic nodes."""

    def test_atomic_node(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    atomic deploy { prompt = "Deploy to production" }
    exit b { prompt = "done" }
    a -> deploy
    deploy -> b
}"""
        flow = parse_flow(source)
        assert "deploy" in flow.nodes
        node = flow.nodes["deploy"]
        assert node.node_type == NodeType.ATOMIC
        assert node.prompt == "Deploy to production"

    def test_atomic_node_with_cwd(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    atomic deploy { prompt = "Deploy" cwd = "./deploy" }
    exit b { prompt = "done" }
    a -> deploy
    deploy -> b
}"""
        flow = parse_flow(source)
        node = flow.nodes["deploy"]
        assert node.cwd == "./deploy"

    def test_atomic_node_with_judge(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    atomic deploy { prompt = "Deploy" judge = true }
    exit b { prompt = "done" }
    a -> deploy
    deploy -> b
}"""
        flow = parse_flow(source)
        node = flow.nodes["deploy"]
        assert node.judge is True

    def test_atomic_node_has_line_info(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    entry a { prompt = "start" }
    atomic x { prompt = "work" }
    exit b { prompt = "done" }
    a -> x
    x -> b
}"""
        flow = parse_flow(source)
        assert flow.nodes["x"].line > 0


class TestMaxParallel:
    """Test parsing of max_parallel flow attribute."""

    def test_max_parallel_parsed(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    max_parallel = 3
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
        flow = parse_flow(source)
        assert flow.max_parallel == 3

    def test_max_parallel_default_is_1(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.max_parallel == 1

    def test_max_parallel_large_value(self):
        source = """flow f {
    budget = 1h
    on_error = pause
    context = handoff
    max_parallel = 100
    entry a { prompt = "x" }
    exit b { prompt = "y" }
    a -> b
}"""
        flow = parse_flow(source)
        assert flow.max_parallel == 100


class TestMixedNewNodeTypes:
    """Test flows that combine new node types together."""

    def test_all_new_node_types_in_one_flow(self):
        source = """flow pipeline {
    budget = 2h
    on_error = pause
    context = handoff
    max_parallel = 2
    entry start { prompt = "begin" }
    wait cooldown { delay = 5m }
    fence barrier { }
    atomic deploy { prompt = "Deploy safely" }
    exit done { prompt = "finished" }
    start -> cooldown
    cooldown -> barrier
    barrier -> deploy
    deploy -> done
}"""
        flow = parse_flow(source)
        assert flow.max_parallel == 2
        assert len(flow.nodes) == 5
        assert flow.nodes["cooldown"].node_type == NodeType.WAIT
        assert flow.nodes["barrier"].node_type == NodeType.FENCE
        assert flow.nodes["deploy"].node_type == NodeType.ATOMIC
        assert flow.nodes["cooldown"].wait_delay_seconds == 300


# ---------------------------------------------------------------------------
# Harness attribute
# ---------------------------------------------------------------------------


class TestHarness:
    """Test harness string attribute at flow level and node level."""

    def test_flow_harness_from_fixture(self):
        flow = parse_flow(load_fixture("valid_harness.flow"))
        assert flow.harness == "gemini"

    def test_flow_harness_nodes_from_fixture(self):
        flow = parse_flow(load_fixture("valid_harness.flow"))
        assert flow.nodes["prepare"].harness == "custom_runner"
        assert flow.nodes["train"].harness is None  # inherits from flow
        assert flow.nodes["evaluate"].harness == "claude"

    def test_flow_harness_default_is_claude(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.harness == "claude"

    def test_flow_harness_custom(self):
        source = (
            'flow f { budget = 1h on_error = pause context = handoff harness = "gemini" '
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.harness == "gemini"

    def test_node_harness_override(self):
        source = (
            'flow f { budget = 1h on_error = pause context = handoff harness = "gemini" '
            'entry a { prompt = "x" harness = "custom" } '
            'exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.harness == "gemini"
        assert flow.nodes["a"].harness == "custom"
        assert flow.nodes["b"].harness is None

    def test_node_harness_default_is_none(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].harness is None
        assert flow.nodes["b"].harness is None

    def test_task_node_harness(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } '
            'task t { prompt = "work" harness = "o3" } '
            'exit b { prompt = "y" } a -> t t -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["t"].harness == "o3"

    def test_atomic_node_harness(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } '
            'atomic d { prompt = "deploy" harness = "gpt4" } '
            'exit b { prompt = "y" } a -> d d -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["d"].harness == "gpt4"


# ---------------------------------------------------------------------------
# Additional: subtasks boolean parameter
# ---------------------------------------------------------------------------


class TestSubtasksParameter:
    """Test subtasks boolean parameter at flow level and node level."""

    def test_flow_subtasks_true(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff subtasks = true "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.subtasks is True

    def test_flow_subtasks_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff subtasks = false "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.subtasks is False

    def test_flow_subtasks_default_is_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.subtasks is False

    def test_node_subtasks_true(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" subtasks = true } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].subtasks is True

    def test_node_subtasks_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" subtasks = false } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].subtasks is False

    def test_node_subtasks_default_is_none(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].subtasks is None
        assert flow.nodes["b"].subtasks is None

    def test_flow_and_node_subtasks_together(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff subtasks = true "
            'entry a { prompt = "x" subtasks = false } '
            'task b { prompt = "y" } '
            'exit c { prompt = "z" } a -> b b -> c }'
        )
        flow = parse_flow(source)
        assert flow.subtasks is True
        assert flow.nodes["a"].subtasks is False
        assert flow.nodes["b"].subtasks is None  # inherits from flow
        assert flow.nodes["c"].subtasks is None

    def test_task_node_subtasks(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } '
            'task t { prompt = "work" subtasks = true } '
            'exit b { prompt = "y" } a -> t t -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["t"].subtasks is True

    def test_atomic_node_subtasks(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } '
            'atomic d { prompt = "deploy" subtasks = true } '
            'exit b { prompt = "y" } a -> d d -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["d"].subtasks is True

    def test_wait_node_cannot_have_subtasks(self):
        """wait nodes use wait_body, not node_body, so subtasks is not accepted."""
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } '
            "wait w { delay = 5s subtasks = true } "
            'exit b { prompt = "y" } a -> w w -> b }'
        )
        with pytest.raises(FlowParseError):
            parse_flow(source)

    def test_fence_node_cannot_have_subtasks(self):
        """fence nodes have empty body, so subtasks is not accepted."""
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } '
            "fence g { subtasks = true } "
            'exit b { prompt = "y" } a -> g g -> b }'
        )
        with pytest.raises(FlowParseError):
            parse_flow(source)


# ---------------------------------------------------------------------------
# Additional: sandbox and sandbox_policy attributes (DSL-008)
# ---------------------------------------------------------------------------


class TestSandboxParameter:
    """Test sandbox boolean and sandbox_policy string at flow level and node level."""

    def test_flow_sandbox_true(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff sandbox = true "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.sandbox is True

    def test_flow_sandbox_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff sandbox = false "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.sandbox is False

    def test_flow_sandbox_default_is_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.sandbox is False

    def test_flow_sandbox_policy_parses(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff sandbox = true "
            'sandbox_policy = "policies/strict.yaml" '
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.sandbox_policy == "policies/strict.yaml"

    def test_flow_sandbox_policy_default_is_none(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.sandbox_policy is None

    def test_node_sandbox_true_on_entry(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff sandbox = true "
            'entry a { prompt = "x" sandbox = true } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].sandbox is True

    def test_node_sandbox_false_on_task(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff sandbox = true "
            'entry a { prompt = "x" } '
            'task t { prompt = "work" sandbox = false } '
            'exit b { prompt = "y" } a -> t t -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["t"].sandbox is False

    def test_node_sandbox_true_on_exit(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff sandbox = true "
            'entry a { prompt = "x" } exit b { prompt = "y" sandbox = true } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["b"].sandbox is True

    def test_node_sandbox_true_on_atomic(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff sandbox = true "
            'entry a { prompt = "x" } '
            'atomic d { prompt = "deploy" sandbox = true } '
            'exit b { prompt = "y" } a -> d d -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["d"].sandbox is True

    def test_node_sandbox_default_is_none(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].sandbox is None
        assert flow.nodes["b"].sandbox is None

    def test_node_sandbox_policy_parses(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff sandbox = true "
            'entry a { prompt = "x" sandbox = true sandbox_policy = "node-policy.yaml" } '
            'exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].sandbox_policy == "node-policy.yaml"

    def test_node_sandbox_policy_default_is_none(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].sandbox_policy is None
        assert flow.nodes["b"].sandbox_policy is None

    def test_flow_and_node_sandbox_together(self):
        """TEST-19: Combined flow and node sandbox attributes parse together."""
        source = (
            "flow f { budget = 1h on_error = pause context = handoff sandbox = true "
            'sandbox_policy = "flow.yaml" '
            'entry a { prompt = "x" } '
            'task t { prompt = "work" sandbox = false } '
            'exit b { prompt = "y" } a -> t t -> b }'
        )
        flow = parse_flow(source)
        assert flow.sandbox is True
        assert flow.sandbox_policy == "flow.yaml"
        assert flow.nodes["t"].sandbox is False
        assert flow.nodes["t"].sandbox_policy is None

    def test_fixture_flow_sandbox(self):
        """Parse the valid_sandbox.flow fixture and verify flow-level sandbox attrs."""
        flow = parse_flow(load_fixture("valid_sandbox.flow"))
        assert flow.sandbox is True
        assert flow.sandbox_policy == "policies/strict.yaml"

    def test_fixture_node_sandbox(self):
        """Parse the valid_sandbox.flow fixture and verify node-level sandbox attrs."""
        flow = parse_flow(load_fixture("valid_sandbox.flow"))
        assert flow.nodes["prepare"].sandbox is True
        assert flow.nodes["prepare"].sandbox_policy == "node-policy.yaml"
        assert flow.nodes["build"].sandbox is False
        assert flow.nodes["build"].sandbox_policy is None
        assert flow.nodes["test_suite"].sandbox is None  # inherits from flow
        assert flow.nodes["test_suite"].sandbox_policy is None
        assert flow.nodes["deploy"].sandbox is True
        assert flow.nodes["deploy"].sandbox_policy is None


# ---------------------------------------------------------------------------
# Additional: lumon and lumon_config attributes (DSL-014)
# ---------------------------------------------------------------------------


class TestLumonParameter:
    """Test lumon boolean and lumon_config string at flow level and node level."""

    def test_flow_lumon_true(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff lumon = true "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.lumon is True

    def test_flow_lumon_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff lumon = false "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.lumon is False

    def test_flow_lumon_default_is_false(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.lumon is False

    def test_flow_lumon_config_parses(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff lumon = true "
            'lumon_config = "security/strict.lumon.json" '
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.lumon_config == "security/strict.lumon.json"

    def test_flow_lumon_config_default_is_none(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.lumon_config is None

    def test_node_lumon_true_on_entry(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff lumon = true "
            'entry a { prompt = "x" lumon = true } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].lumon is True

    def test_node_lumon_false_on_task(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff lumon = true "
            'entry a { prompt = "x" } '
            'task t { prompt = "work" lumon = false } '
            'exit b { prompt = "y" } a -> t t -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["t"].lumon is False

    def test_node_lumon_true_on_exit(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff lumon = true "
            'entry a { prompt = "x" } exit b { prompt = "y" lumon = true } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["b"].lumon is True

    def test_node_lumon_true_on_atomic(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff lumon = true "
            'entry a { prompt = "x" } '
            'atomic d { prompt = "deploy" lumon = true } '
            'exit b { prompt = "y" } a -> d d -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["d"].lumon is True

    def test_node_lumon_default_is_none(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].lumon is None
        assert flow.nodes["b"].lumon is None

    def test_node_lumon_config_parses(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff lumon = true "
            'entry a { prompt = "x" lumon = true lumon_config = "node-lumon.json" } '
            'exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].lumon_config == "node-lumon.json"

    def test_node_lumon_config_default_is_none(self):
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            'entry a { prompt = "x" } exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.nodes["a"].lumon_config is None
        assert flow.nodes["b"].lumon_config is None

    def test_flow_and_node_lumon_together(self):
        """Combined flow and node lumon attributes parse together."""
        source = (
            "flow f { budget = 1h on_error = pause context = handoff lumon = true "
            'lumon_config = "flow.lumon.json" '
            'entry a { prompt = "x" } '
            'task t { prompt = "work" lumon = false } '
            'exit b { prompt = "y" } a -> t t -> b }'
        )
        flow = parse_flow(source)
        assert flow.lumon is True
        assert flow.lumon_config == "flow.lumon.json"
        assert flow.nodes["t"].lumon is False
        assert flow.nodes["t"].lumon_config is None

    def test_fixture_flow_lumon(self):
        """Parse the valid_lumon.flow fixture and verify flow-level lumon attrs."""
        flow = parse_flow(load_fixture("valid_lumon.flow"))
        assert flow.lumon is True
        assert flow.lumon_config == "security/strict.lumon.json"

    def test_fixture_node_lumon(self):
        """Parse the valid_lumon.flow fixture and verify node-level lumon attrs."""
        flow = parse_flow(load_fixture("valid_lumon.flow"))
        assert flow.nodes["prepare"].lumon is True
        assert flow.nodes["prepare"].lumon_config == "node-lumon.json"
        assert flow.nodes["build"].lumon is False
        assert flow.nodes["build"].lumon_config is None
        assert flow.nodes["test_suite"].lumon is None  # inherits from flow
        assert flow.nodes["test_suite"].lumon_config is None
        assert flow.nodes["deploy"].lumon is True
        assert flow.nodes["deploy"].lumon_config is None

    def test_sandbox_and_lumon_together(self):
        """Both sandbox and lumon can be enabled on the same flow/node (layered security)."""
        source = (
            "flow f { budget = 1h on_error = pause context = handoff "
            "sandbox = true lumon = true "
            'entry a { prompt = "x" sandbox = true lumon = true } '
            'exit b { prompt = "y" } a -> b }'
        )
        flow = parse_flow(source)
        assert flow.sandbox is True
        assert flow.lumon is True
        assert flow.nodes["a"].sandbox is True
        assert flow.nodes["a"].lumon is True
