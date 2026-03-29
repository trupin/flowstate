"""Tests for judge protocol -- prompt construction, schema building, decision parsing.

The file-based write/read functions (write_task_input, create_judge_dir,
write_judge_request, write_judge_decision, read_judge_decision) were removed
in ENGINE-068 when all artifact I/O moved to the database. This file retains
tests for the remaining judge protocol code (prompt building, schema building,
decision parsing via JudgeProtocol).
"""

from __future__ import annotations

from flowstate.engine.judge import (
    JudgeContext,
    JudgeDecision,
    build_judge_prompt,
    build_judge_schema,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_judge_context(
    *,
    node_name: str = "review",
    task_prompt: str = "Review the code changes",
    exit_code: int = 0,
    summary: str | None = "All tests pass.",
    task_cwd: str = "/workspace/project",
    run_id: str = "run-abc",
    outgoing_edges: list[tuple[str, str]] | None = None,
) -> JudgeContext:
    if outgoing_edges is None:
        outgoing_edges = [
            ("tests pass", "deploy"),
            ("tests fail", "fix"),
        ]
    return JudgeContext(
        node_name=node_name,
        task_prompt=task_prompt,
        exit_code=exit_code,
        summary=summary,
        task_cwd=task_cwd,
        run_id=run_id,
        outgoing_edges=outgoing_edges,
    )


# ---------------------------------------------------------------------------
# Tests: build_judge_prompt
# ---------------------------------------------------------------------------


class TestBuildJudgePrompt:
    def test_prompt_contains_all_fields(self) -> None:
        """Judge prompt includes node name, task prompt, exit code, summary, cwd, transitions."""
        ctx = _make_judge_context()
        prompt = build_judge_prompt(ctx)
        assert "review" in prompt
        assert "Review the code changes" in prompt
        assert "Exit Code: 0" in prompt
        assert "All tests pass." in prompt
        assert "/workspace/project" in prompt
        assert "tests pass" in prompt
        assert "tests fail" in prompt
        assert "deploy" in prompt
        assert "fix" in prompt

    def test_prompt_no_summary(self) -> None:
        """Judge prompt with summary=None includes fallback text."""
        ctx = _make_judge_context(summary=None)
        prompt = build_judge_prompt(ctx)
        assert "(No summary was written by the task)" in prompt


# ---------------------------------------------------------------------------
# Tests: build_judge_schema
# ---------------------------------------------------------------------------


class TestBuildJudgeSchema:
    def test_schema_includes_targets_and_none(self) -> None:
        """Schema enum includes all target nodes plus __none__."""
        edges = [("tests pass", "deploy"), ("tests fail", "fix")]
        schema = build_judge_schema(edges)
        enum_values = schema["properties"]["decision"]["enum"]  # type: ignore[index]
        assert "deploy" in enum_values
        assert "fix" in enum_values
        assert "__none__" in enum_values

    def test_schema_deduplicates_targets(self) -> None:
        """Duplicate targets in edges are deduplicated in the schema enum."""
        edges = [("condition_a", "target"), ("condition_b", "target")]
        schema = build_judge_schema(edges)
        enum_values = schema["properties"]["decision"]["enum"]  # type: ignore[index]
        assert enum_values.count("target") == 1


# ---------------------------------------------------------------------------
# Tests: JudgeDecision properties
# ---------------------------------------------------------------------------


class TestJudgeDecision:
    def test_is_none(self) -> None:
        decision = JudgeDecision(target="__none__", reasoning="No match", confidence=0.5)
        assert decision.is_none is True

    def test_is_not_none(self) -> None:
        decision = JudgeDecision(target="deploy", reasoning="All good", confidence=0.9)
        assert decision.is_none is False

    def test_is_low_confidence(self) -> None:
        decision = JudgeDecision(target="deploy", reasoning="Maybe", confidence=0.3)
        assert decision.is_low_confidence is True

    def test_is_not_low_confidence(self) -> None:
        decision = JudgeDecision(target="deploy", reasoning="Sure", confidence=0.8)
        assert decision.is_low_confidence is False
