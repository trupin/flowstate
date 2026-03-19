"""Tests for the orchestrator judge path -- evaluate_via_orchestrator, fallback,
build_judge_instruction, and backward compatibility of evaluate().
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from flowstate.engine.judge import (
    JudgeContext,
    JudgePauseError,
    JudgeProtocol,
    write_judge_decision,
)
from flowstate.engine.orchestrator import OrchestratorSession, build_judge_instruction
from flowstate.engine.subprocess_mgr import JudgeError, JudgeResult, SubprocessManager

if TYPE_CHECKING:
    from pathlib import Path


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
    skip_permissions: bool = False,
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
        skip_permissions=skip_permissions,
    )


def _make_session(
    *,
    session_id: str = "test-session-123",
    harness: str = "claude",
    cwd: str = "/workspace/project",
    data_dir: str = "/tmp/orch",
) -> OrchestratorSession:
    return OrchestratorSession(
        session_id=session_id,
        harness=harness,
        cwd=cwd,
        data_dir=data_dir,
        is_initialized=True,
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


async def _empty_stream():
    """An async generator that yields nothing (simulates completed stream)."""
    return
    yield  # make this an async generator


def _mock_subprocess_mgr(**run_judge_kwargs: object) -> SubprocessManager:
    """Create a SubprocessManager mock with run_judge and run_task_resume."""
    mgr = AsyncMock(spec=SubprocessManager)
    mgr.run_judge = AsyncMock(**run_judge_kwargs)
    mgr.run_task_resume = MagicMock(return_value=_empty_stream())
    return mgr


# ---------------------------------------------------------------------------
# build_judge_instruction tests
# ---------------------------------------------------------------------------


class TestBuildJudgeInstruction:
    def test_contains_node_name(self) -> None:
        instruction = build_judge_instruction(
            "review", "/tmp/req.md", "/tmp/dec.json", ["deploy", "fix"]
        )
        assert 'task "review"' in instruction

    def test_contains_request_path(self) -> None:
        instruction = build_judge_instruction(
            "review", "/tmp/req.md", "/tmp/dec.json", ["deploy", "fix"]
        )
        assert "/tmp/req.md" in instruction

    def test_contains_decision_path(self) -> None:
        instruction = build_judge_instruction(
            "review", "/tmp/req.md", "/tmp/dec.json", ["deploy", "fix"]
        )
        assert "/tmp/dec.json" in instruction

    def test_contains_targets(self) -> None:
        instruction = build_judge_instruction(
            "review", "/tmp/req.md", "/tmp/dec.json", ["deploy", "fix"]
        )
        assert '"deploy"' in instruction
        assert '"fix"' in instruction

    def test_contains_none_target(self) -> None:
        instruction = build_judge_instruction("review", "/tmp/req.md", "/tmp/dec.json", ["deploy"])
        assert '"__none__"' in instruction

    def test_contains_json_format(self) -> None:
        instruction = build_judge_instruction("review", "/tmp/req.md", "/tmp/dec.json", ["deploy"])
        assert '"decision"' in instruction
        assert '"reasoning"' in instruction
        assert '"confidence"' in instruction


# ---------------------------------------------------------------------------
# evaluate_via_orchestrator tests
# ---------------------------------------------------------------------------


class TestEvaluateViaOrchestrator:
    @pytest.mark.asyncio
    async def test_writes_request_and_reads_decision(self, tmp_path: Path) -> None:
        """evaluate_via_orchestrator writes REQUEST.md and reads DECISION.json."""
        context = _make_context()
        session = _make_session()
        run_data_dir = str(tmp_path)

        # Pre-write DECISION.json where create_judge_dir will create it
        judge_dir = tmp_path / "judge" / "review-1"
        judge_dir.mkdir(parents=True, exist_ok=True)
        write_judge_decision(str(judge_dir), "deploy", "Tests pass", 0.92)

        mgr = _mock_subprocess_mgr()
        judge = JudgeProtocol(mgr)

        decision = await judge.evaluate_via_orchestrator(context, session, run_data_dir)

        assert decision.target == "deploy"
        assert decision.reasoning == "Tests pass"
        assert decision.confidence == 0.92

        # Verify REQUEST.md was written
        request_path = judge_dir / "REQUEST.md"
        assert request_path.exists()
        request_content = request_path.read_text()
        assert "review" in request_content

    @pytest.mark.asyncio
    async def test_resumes_orchestrator_session(self, tmp_path: Path) -> None:
        """evaluate_via_orchestrator calls run_task_resume with correct session_id."""
        context = _make_context()
        session = _make_session(session_id="my-session-42")
        run_data_dir = str(tmp_path)

        # Pre-write DECISION.json
        judge_dir = tmp_path / "judge" / "review-1"
        judge_dir.mkdir(parents=True, exist_ok=True)
        write_judge_decision(str(judge_dir), "deploy", "ok", 0.9)

        mgr = _mock_subprocess_mgr()
        judge = JudgeProtocol(mgr)

        await judge.evaluate_via_orchestrator(context, session, run_data_dir)

        mgr.run_task_resume.assert_called_once()
        call_args = mgr.run_task_resume.call_args
        # Third positional arg is the session_id
        assert call_args[0][2] == "my-session-42"

    @pytest.mark.asyncio
    async def test_instruction_contains_targets(self, tmp_path: Path) -> None:
        """The instruction passed to run_task_resume contains target node names."""
        context = _make_context(outgoing_edges=[("approved", "deploy"), ("rejected", "fix")])
        session = _make_session()
        run_data_dir = str(tmp_path)

        # Pre-write DECISION.json
        judge_dir = tmp_path / "judge" / "review-1"
        judge_dir.mkdir(parents=True, exist_ok=True)
        write_judge_decision(str(judge_dir), "deploy", "ok", 0.9)

        mgr = _mock_subprocess_mgr()
        judge = JudgeProtocol(mgr)

        await judge.evaluate_via_orchestrator(context, session, run_data_dir)

        # First positional arg is the instruction
        instruction = mgr.run_task_resume.call_args[0][0]
        assert '"deploy"' in instruction
        assert '"fix"' in instruction

    @pytest.mark.asyncio
    async def test_raises_on_missing_decision(self, tmp_path: Path) -> None:
        """evaluate_via_orchestrator raises FileNotFoundError if DECISION.json is missing."""
        context = _make_context()
        session = _make_session()
        run_data_dir = str(tmp_path)

        # Do NOT pre-write DECISION.json
        mgr = _mock_subprocess_mgr()
        judge = JudgeProtocol(mgr)

        with pytest.raises(FileNotFoundError):
            await judge.evaluate_via_orchestrator(context, session, run_data_dir)

    @pytest.mark.asyncio
    async def test_raises_on_malformed_decision(self, tmp_path: Path) -> None:
        """evaluate_via_orchestrator raises ValueError if DECISION.json is invalid."""
        context = _make_context()
        session = _make_session()
        run_data_dir = str(tmp_path)

        # Pre-write malformed DECISION.json
        judge_dir = tmp_path / "judge" / "review-1"
        judge_dir.mkdir(parents=True, exist_ok=True)
        (judge_dir / "DECISION.json").write_text("not json")

        mgr = _mock_subprocess_mgr()
        judge = JudgeProtocol(mgr)

        with pytest.raises(ValueError, match="invalid JSON"):
            await judge.evaluate_via_orchestrator(context, session, run_data_dir)


# ---------------------------------------------------------------------------
# evaluate() with orchestrator integration
# ---------------------------------------------------------------------------


class TestEvaluateWithOrchestrator:
    @pytest.mark.asyncio
    async def test_orchestrator_path_used_when_session_provided(self, tmp_path: Path) -> None:
        """evaluate() uses orchestrator path when session and run_data_dir are given."""
        context = _make_context()
        session = _make_session()
        run_data_dir = str(tmp_path)

        # Pre-write DECISION.json
        judge_dir = tmp_path / "judge" / "review-1"
        judge_dir.mkdir(parents=True, exist_ok=True)
        write_judge_decision(str(judge_dir), "deploy", "Tests pass", 0.95)

        mgr = _mock_subprocess_mgr()
        judge = JudgeProtocol(mgr)

        decision = await judge.evaluate(
            context,
            orchestrator_session=session,
            run_data_dir=run_data_dir,
        )

        assert decision.target == "deploy"
        # run_judge should NOT have been called (orchestrator path succeeded)
        mgr.run_judge.assert_not_called()

    @pytest.mark.asyncio
    async def test_orchestrator_fallback_to_subprocess(self, tmp_path: Path) -> None:
        """When orchestrator fails, evaluate() falls back to direct subprocess."""
        context = _make_context()
        session = _make_session()
        run_data_dir = str(tmp_path)

        # Do NOT pre-write DECISION.json -> orchestrator path will fail
        good_result = _make_judge_result(decision="deploy", reasoning="ok", confidence=0.9)
        mgr = _mock_subprocess_mgr(return_value=good_result)
        judge = JudgeProtocol(mgr)

        decision = await judge.evaluate(
            context,
            orchestrator_session=session,
            run_data_dir=run_data_dir,
        )

        assert decision.target == "deploy"
        # run_judge SHOULD have been called as fallback
        mgr.run_judge.assert_called_once()

    @pytest.mark.asyncio
    async def test_orchestrator_fallback_preserves_retry(self, tmp_path: Path) -> None:
        """When orchestrator fails and first subprocess fails, retry still works."""
        context = _make_context()
        session = _make_session()
        run_data_dir = str(tmp_path)

        # Orchestrator path will fail (no DECISION.json)
        good_result = _make_judge_result(decision="deploy", reasoning="ok", confidence=0.9)
        mgr = _mock_subprocess_mgr(
            side_effect=[
                JudgeError("first fail", exit_code=1),
                good_result,
            ]
        )
        judge = JudgeProtocol(mgr)

        decision = await judge.evaluate(
            context,
            orchestrator_session=session,
            run_data_dir=run_data_dir,
        )

        assert decision.target == "deploy"
        assert mgr.run_judge.call_count == 2


# ---------------------------------------------------------------------------
# evaluate() without orchestrator (backward compatibility)
# ---------------------------------------------------------------------------


class TestEvaluateWithoutOrchestrator:
    @pytest.mark.asyncio
    async def test_evaluate_no_orchestrator_args(self) -> None:
        """evaluate() works the same as before when no orchestrator args are given."""
        result = _make_judge_result(decision="deploy", reasoning="ok", confidence=0.9)
        mgr = _mock_subprocess_mgr(return_value=result)
        judge = JudgeProtocol(mgr)

        decision = await judge.evaluate(_make_context())

        assert decision.target == "deploy"
        mgr.run_judge.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_retry_still_works(self) -> None:
        """Retry logic unchanged when no orchestrator is present."""
        good_result = _make_judge_result()
        mgr = _mock_subprocess_mgr(
            side_effect=[
                JudgeError("boom", exit_code=1),
                good_result,
            ],
        )
        judge = JudgeProtocol(mgr)

        decision = await judge.evaluate(_make_context())

        assert decision.target == "deploy"
        assert mgr.run_judge.call_count == 2

    @pytest.mark.asyncio
    async def test_evaluate_pause_on_double_failure(self) -> None:
        """JudgePauseError still raised on double failure without orchestrator."""
        mgr = _mock_subprocess_mgr(
            side_effect=[
                JudgeError("boom1", exit_code=1),
                JudgeError("boom2", exit_code=1),
            ],
        )
        judge = JudgeProtocol(mgr)

        with pytest.raises(JudgePauseError):
            await judge.evaluate(_make_context())

    @pytest.mark.asyncio
    async def test_evaluate_none_session_ignored(self) -> None:
        """Passing orchestrator_session=None has no effect (backward compat)."""
        result = _make_judge_result(decision="fix", reasoning="tests fail", confidence=0.8)
        mgr = _mock_subprocess_mgr(return_value=result)
        judge = JudgeProtocol(mgr)

        decision = await judge.evaluate(
            _make_context(),
            orchestrator_session=None,
            run_data_dir=None,
        )

        assert decision.target == "fix"
        mgr.run_judge.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_partial_args_ignored(self) -> None:
        """Passing only one of session/data_dir skips orchestrator path."""
        result = _make_judge_result(decision="deploy", reasoning="ok", confidence=0.9)
        mgr = _mock_subprocess_mgr(return_value=result)
        session = _make_session()
        judge = JudgeProtocol(mgr)

        # Session provided but no run_data_dir
        decision = await judge.evaluate(
            _make_context(),
            orchestrator_session=session,
            run_data_dir=None,
        )

        assert decision.target == "deploy"
        mgr.run_judge.assert_called_once()
