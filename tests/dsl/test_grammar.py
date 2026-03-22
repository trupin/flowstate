"""Tests for the Flowstate DSL Lark grammar (DSL-001).

Verifies that the grammar file loads correctly and can parse all valid
Flowstate DSL constructs, including all 6 Appendix A examples from specs.md.
"""

from pathlib import Path

import pytest
from lark import Lark
from lark.exceptions import UnexpectedInput

GRAMMAR_PATH = Path(__file__).resolve().parents[2] / "src" / "flowstate" / "dsl" / "grammar.lark"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def parser() -> Lark:
    """Load the Lark grammar with the Earley parser."""
    grammar_text = GRAMMAR_PATH.read_text()
    return Lark(grammar_text, parser="earley")


class TestGrammarLoads:
    def test_grammar_file_exists(self) -> None:
        assert GRAMMAR_PATH.exists(), f"Grammar file not found at {GRAMMAR_PATH}"

    def test_grammar_loads_earley(self) -> None:
        grammar_text = GRAMMAR_PATH.read_text()
        lark = Lark(grammar_text, parser="earley")
        assert lark is not None


class TestParseMinimalFlow:
    def test_minimal_flow(self, parser: Lark) -> None:
        source = """
        flow minimal {
            budget = 10m
            on_error = pause
            context = handoff

            entry start {
                prompt = "Begin."
            }

            exit done {
                prompt = "End."
            }

            start -> done
        }
        """
        tree = parser.parse(source)
        assert tree is not None
        assert tree.data == "start"


class TestParseAppendixExamples:
    """Parse all 6 Appendix A examples from specs.md."""

    @pytest.fixture(
        params=[
            "valid_linear.flow",
            "valid_fork_join.flow",
            "valid_cycle.flow",
            "valid_fork_join_cycle.flow",
            "valid_scheduled_deploy.flow",
            "valid_recurring_audit.flow",
        ]
    )
    def fixture_source(self, request: pytest.FixtureRequest) -> str:
        fixture_file = FIXTURES_DIR / request.param
        assert fixture_file.exists(), f"Fixture not found: {fixture_file}"
        return fixture_file.read_text()

    def test_appendix_examples_parse(self, parser: Lark, fixture_source: str) -> None:
        tree = parser.parse(fixture_source)
        assert tree is not None
        assert tree.data == "start"

    def test_a1_linear(self, parser: Lark) -> None:
        source = (FIXTURES_DIR / "valid_linear.flow").read_text()
        tree = parser.parse(source)
        assert tree.data == "start"

    def test_a2_fork_join(self, parser: Lark) -> None:
        source = (FIXTURES_DIR / "valid_fork_join.flow").read_text()
        tree = parser.parse(source)
        assert tree.data == "start"

    def test_a3_cycle(self, parser: Lark) -> None:
        source = (FIXTURES_DIR / "valid_cycle.flow").read_text()
        tree = parser.parse(source)
        assert tree.data == "start"

    def test_a4_fork_join_cycle(self, parser: Lark) -> None:
        source = (FIXTURES_DIR / "valid_fork_join_cycle.flow").read_text()
        tree = parser.parse(source)
        assert tree.data == "start"

    def test_a5_scheduled_deploy(self, parser: Lark) -> None:
        source = (FIXTURES_DIR / "valid_scheduled_deploy.flow").read_text()
        tree = parser.parse(source)
        assert tree.data == "start"

    def test_a6_recurring_audit(self, parser: Lark) -> None:
        source = (FIXTURES_DIR / "valid_recurring_audit.flow").read_text()
        tree = parser.parse(source)
        assert tree.data == "start"


class TestStringHandling:
    def test_double_quoted_string(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s {
                prompt = "Hello world"
            }

            exit e {
                prompt = "Done"
            }

            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_triple_quoted_string(self, parser: Lark) -> None:
        source = '''
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s {
                prompt = """
                Multi-line prompt.
                With several lines.
                """
            }

            exit e {
                prompt = "Done"
            }

            s -> e
        }
        '''
        tree = parser.parse(source)
        assert tree is not None

    def test_triple_quoted_with_inner_quotes(self, parser: Lark) -> None:
        source = '''
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s {
                prompt = """She said "hello" and he said "goodbye"."""
            }

            exit e {
                prompt = "Done"
            }

            s -> e
        }
        '''
        tree = parser.parse(source)
        assert tree is not None

    def test_template_variables_in_strings(self, parser: Lark) -> None:
        source = '''
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                focus: string = "all"
            }

            entry s {
                prompt = """Analyze {{focus}} carefully."""
            }

            exit e {
                prompt = "Done"
            }

            s -> e
        }
        '''
        tree = parser.parse(source)
        assert tree is not None


class TestDurationTokens:
    def test_seconds(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 30s
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_minutes(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 5m
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_hours(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 2h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_zero_seconds(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 0s
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_large_duration(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 999h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None


class TestEdgeConfigBlocks:
    def test_context_config(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e { context = session }
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_delay_config(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e { delay = 5m }
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_schedule_config(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e { schedule = "0 2 * * *" }
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_empty_config_block(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e {}
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_conditional_edge_with_config(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e when "condition met" { context = handoff }
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_multiple_edge_attrs(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e { context = handoff delay = 5m }
        }
        """
        tree = parser.parse(source)
        assert tree is not None


class TestComments:
    def test_comment_on_own_line(self, parser: Lark) -> None:
        source = """
        // This is a comment
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_comment_at_end_of_line(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h  // time limit
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e  // simple edge
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_multiple_comments(self, parser: Lark) -> None:
        source = """
        // Top comment
        flow t {
            // Flow attributes
            budget = 1h
            on_error = pause
            context = handoff

            // Entry node
            entry s { prompt = "Go" }
            // Exit node
            exit e { prompt = "Done" }
            // Edge
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None


class TestFlowAttributes:
    def test_on_overlap_skip(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff
            on_overlap = skip

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_on_overlap_queue(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff
            on_overlap = queue

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_on_overlap_parallel(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff
            on_overlap = parallel

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_schedule_attribute(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff
            schedule = "0 9 * * MON"
            on_overlap = skip

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_all_error_policies(self, parser: Lark) -> None:
        for policy in ("pause", "abort", "skip"):
            source = f"""
            flow t {{
                budget = 1h
                on_error = {policy}
                context = handoff

                entry s {{ prompt = "Go" }}
                exit e {{ prompt = "Done" }}
                s -> e
            }}
            """
            tree = parser.parse(source)
            assert tree is not None

    def test_all_context_modes(self, parser: Lark) -> None:
        for mode in ("handoff", "session", "none"):
            source = f"""
            flow t {{
                budget = 1h
                on_error = pause
                context = {mode}

                entry s {{ prompt = "Go" }}
                exit e {{ prompt = "Done" }}
                s -> e
            }}
            """
            tree = parser.parse(source)
            assert tree is not None

    def test_workspace_attribute(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff
            workspace = "./project"

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_attributes_any_order(self, parser: Lark) -> None:
        source = """
        flow t {
            context = handoff
            workspace = "./project"
            on_error = pause
            budget = 1h

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None


class TestFlowInputOutputBlocks:
    def test_input_without_defaults(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                target: string
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_input_with_string_default(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                focus: string = "all"
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_input_with_number_default(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                count: number = 5
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_input_with_bool_default(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                verbose: bool = true
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_input_bool_false_default(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                debug: bool = false
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_multiple_input_fields(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                target: string
                count: number = 3
                verbose: bool = false
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_no_input_output(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_output_block(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            output {
                result: string
                score: number
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_input_and_output_blocks(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                title: string
            }
            output {
                result: string
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None


class TestNodeTypes:
    def test_node_with_cwd(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s {
                cwd = "./backend"
                prompt = "Go"
            }
            exit e {
                cwd = "./backend"
                prompt = "Done"
            }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_node_with_prompt_and_cwd(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s {
                prompt = "Go"
                cwd = "./project"
            }
            exit e {
                prompt = "Done"
                cwd = "./project"
            }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None


class TestEdgeTypes:
    def test_unconditional_edge(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_conditional_edge(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e when "tests pass"
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_fork_edge(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            task a { prompt = "A" }
            task b { prompt = "B" }
            exit e { prompt = "Done" }
            s -> [a, b]
            [a, b] -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_join_edge(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            task a { prompt = "A" }
            task b { prompt = "B" }
            task c { prompt = "C" }
            exit e { prompt = "Done" }
            s -> [a, b, c]
            [a, b, c] -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None


class TestSyntaxErrors:
    def test_missing_opening_brace(self, parser: Lark) -> None:
        source = """
        flow t
            budget = 1h
        }
        """
        with pytest.raises(UnexpectedInput):
            parser.parse(source)

    def test_missing_closing_brace(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff
        """
        with pytest.raises(UnexpectedInput):
            parser.parse(source)

    def test_missing_arrow(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s e
        }
        """
        with pytest.raises(UnexpectedInput):
            parser.parse(source)

    def test_invalid_keyword(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff
            invalid_attr = "bad"

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        with pytest.raises(UnexpectedInput):
            parser.parse(source)

    def test_empty_flow(self, parser: Lark) -> None:
        """An empty flow body should parse since flow_body is flow_stmt*."""
        source = """
        flow empty {
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_node_missing_prompt(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            entry s { }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        with pytest.raises(UnexpectedInput):
            parser.parse(source)


class TestDecimalNumbers:
    def test_decimal_number_input_field(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                threshold: number = 0.95
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None

    def test_integer_number_input_field(self, parser: Lark) -> None:
        source = """
        flow t {
            budget = 1h
            on_error = pause
            context = handoff

            input {
                count: number = 10
            }

            entry s { prompt = "Go" }
            exit e { prompt = "Done" }
            s -> e
        }
        """
        tree = parser.parse(source)
        assert tree is not None
