"""Tests for the judge protocol -- prompt construction, schema generation,
evaluation with retry, and failure handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flowstate.engine.judge import (
    JudgeContext,
    JudgePauseError,
    JudgeProtocol,
    build_judge_prompt,
    build_judge_schema,
)
from flowstate.engine.subprocess_mgr import JudgeError, JudgeResult, SubprocessManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
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


def _make_judge_result(
    *,
    decision: str = "deploy",
    reasoning: str = "All tests pass so deploy is appropriate",
    confidence: float = 0.95,
) -> JudgeResult:
    return JudgeResult(
        decision=decision,
        reasoning=reasoning,
        confidence=confidence,
        raw_output="{}",
    )


def _mock_subprocess_mgr(**kwargs: object) -> SubprocessManager:
    """Create a SubprocessManager mock with run_judge as an AsyncMock.

    Note: SubprocessManager will be replaced by SDKRunner once the migration
    is complete. The mock interface is identical.
    """
    mgr = AsyncMock(spec=SubprocessManager)
    mgr.run_judge = AsyncMock(**kwargs)
    return mgr


# ---------------------------------------------------------------------------
# Prompt construction tests
# ---------------------------------------------------------------------------


class TestBuildJudgePrompt:
    def test_build_judge_prompt(self) -> None:
        """Build a prompt with known context and verify all sections are present."""
        ctx = _make_context()
        prompt = build_judge_prompt(ctx)

        # Completed Task section
        assert "## Completed Task" in prompt
        assert "- Name: review" in prompt
        assert "- Prompt: Review the code changes" in prompt
        assert "- Exit Code: 0" in prompt

        # Task Summary section
        assert "## Task Summary" in prompt
        assert "All tests pass." in prompt

        # Task Working Directory section
        assert "## Task Working Directory" in prompt
        assert "/workspace/project" in prompt

        # Available Transitions section
        assert "## Available Transitions" in prompt
        assert '"tests pass"' in prompt
        assert "deploy" in prompt
        assert '"tests fail"' in prompt
        assert "fix" in prompt

        # Instructions section
        assert "## Instructions" in prompt
        assert "__none__" in prompt

    def test_build_judge_prompt_no_summary(self) -> None:
        """Context with summary=None uses fallback text."""
        ctx = _make_context(summary=None)
        prompt = build_judge_prompt(ctx)

        assert "(No summary was written by the task)" in prompt

    def test_build_judge_prompt_multiple_edges(self) -> None:
        """Context with 3 outgoing edges lists all transitions."""
        ctx = _make_context(
            outgoing_edges=[
                ("approved", "deploy"),
                ("needs work", "revise"),
                ("rejected", "cancel"),
            ],
        )
        prompt = build_judge_prompt(ctx)

        assert '"approved"' in prompt
        assert "deploy" in prompt
        assert '"needs work"' in prompt
        assert "revise" in prompt
        assert '"rejected"' in prompt
        assert "cancel" in prompt


# ---------------------------------------------------------------------------
# Schema generation tests
# ---------------------------------------------------------------------------


class TestBuildJudgeSchema:
    def test_build_judge_schema(self) -> None:
        """Schema for 2 edges with different targets includes both + __none__."""
        edges = [("tests pass", "deploy"), ("tests fail", "fix")]
        schema = build_judge_schema(edges)

        assert schema["type"] == "object"
        props = schema["properties"]
        assert isinstance(props, dict)
        decision_prop = props["decision"]
        assert isinstance(decision_prop, dict)
        assert decision_prop["enum"] == ["deploy", "fix", "__none__"]
        assert schema["required"] == ["decision", "reasoning", "confidence"]

    def test_build_judge_schema_deduplicates(self) -> None:
        """Two edges pointing to the same target produce a single enum entry."""
        edges = [("condition a", "same_node"), ("condition b", "same_node")]
        schema = build_judge_schema(edges)

        props = schema["properties"]
        assert isinstance(props, dict)
        decision_prop = props["decision"]
        assert isinstance(decision_prop, dict)
        assert decision_prop["enum"] == ["same_node", "__none__"]


# ---------------------------------------------------------------------------
# Evaluation tests
# ---------------------------------------------------------------------------


class TestJudgeProtocolEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_happy_path(self) -> None:
        """Mock run_judge returns a valid result on first attempt."""
        result = _make_judge_result(decision="deploy", reasoning="Tests pass", confidence=0.95)
        mgr = _mock_subprocess_mgr(return_value=result)
        protocol = JudgeProtocol(mgr)

        decision = await protocol.evaluate(_make_context())

        assert decision.target == "deploy"
        assert decision.reasoning == "Tests pass"
        assert decision.confidence == 0.95
        assert not decision.is_none
        assert not decision.is_low_confidence
        mgr.run_judge.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_none_decision(self) -> None:
        """Decision of __none__ is valid and sets is_none flag."""
        result = _make_judge_result(decision="__none__", reasoning="No match", confidence=0.8)
        mgr = _mock_subprocess_mgr(return_value=result)
        protocol = JudgeProtocol(mgr)

        decision = await protocol.evaluate(_make_context())

        assert decision.target == "__none__"
        assert decision.is_none is True

    @pytest.mark.asyncio
    async def test_evaluate_low_confidence(self) -> None:
        """Confidence below 0.5 sets is_low_confidence flag."""
        result = _make_judge_result(decision="deploy", reasoning="Unsure", confidence=0.3)
        mgr = _mock_subprocess_mgr(return_value=result)
        protocol = JudgeProtocol(mgr)

        decision = await protocol.evaluate(_make_context())

        assert decision.confidence == 0.3
        assert decision.is_low_confidence is True

    @pytest.mark.asyncio
    async def test_evaluate_confidence_exactly_0_5(self) -> None:
        """Confidence of exactly 0.5 is NOT low confidence (threshold is < 0.5)."""
        result = _make_judge_result(decision="deploy", reasoning="Borderline", confidence=0.5)
        mgr = _mock_subprocess_mgr(return_value=result)
        protocol = JudgeProtocol(mgr)

        decision = await protocol.evaluate(_make_context())

        assert decision.confidence == 0.5
        assert decision.is_low_confidence is False

    @pytest.mark.asyncio
    async def test_evaluate_retry_on_first_failure(self) -> None:
        """First call raises JudgeError; second call succeeds."""
        valid_result = _make_judge_result()
        mgr = _mock_subprocess_mgr(
            side_effect=[
                JudgeError("crash", exit_code=1),
                valid_result,
            ],
        )
        protocol = JudgeProtocol(mgr)

        decision = await protocol.evaluate(_make_context())

        assert decision.target == "deploy"
        assert mgr.run_judge.call_count == 2

    @pytest.mark.asyncio
    async def test_evaluate_pause_on_double_failure(self) -> None:
        """Both calls raise JudgeError -- JudgePauseError is raised."""
        mgr = _mock_subprocess_mgr(
            side_effect=[
                JudgeError("crash 1", exit_code=1),
                JudgeError("crash 2", exit_code=1),
            ],
        )
        protocol = JudgeProtocol(mgr)

        with pytest.raises(JudgePauseError):
            await protocol.evaluate(_make_context())

        assert mgr.run_judge.call_count == 2

    @pytest.mark.asyncio
    async def test_evaluate_retry_on_invalid_target(self) -> None:
        """First call returns an invalid target (triggers ValueError); retry succeeds."""
        bad_result = _make_judge_result(decision="nonexistent_node")
        good_result = _make_judge_result(decision="deploy")
        mgr = _mock_subprocess_mgr(side_effect=[bad_result, good_result])
        protocol = JudgeProtocol(mgr)

        decision = await protocol.evaluate(_make_context())

        assert decision.target == "deploy"
        assert mgr.run_judge.call_count == 2

    @pytest.mark.asyncio
    async def test_evaluate_invalid_confidence_range(self) -> None:
        """Confidence > 1.0 triggers ValueError and retry."""
        bad_result = _make_judge_result(confidence=1.5)
        good_result = _make_judge_result(confidence=0.9)
        mgr = _mock_subprocess_mgr(side_effect=[bad_result, good_result])
        protocol = JudgeProtocol(mgr)

        decision = await protocol.evaluate(_make_context())

        assert decision.confidence == 0.9
        assert mgr.run_judge.call_count == 2

    @pytest.mark.asyncio
    async def test_evaluate_pause_reason_message(self) -> None:
        """On double failure, JudgePauseError.reason contains useful info."""
        mgr = _mock_subprocess_mgr(
            side_effect=[
                JudgeError("first boom", exit_code=1),
                JudgeError("second boom", exit_code=1),
            ],
        )
        protocol = JudgeProtocol(mgr)

        with pytest.raises(JudgePauseError) as exc_info:
            await protocol.evaluate(_make_context())

        assert "Judge failed after retry" in exc_info.value.reason
        assert "second boom" in exc_info.value.reason
