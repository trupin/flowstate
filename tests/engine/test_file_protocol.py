"""Tests for file communication protocol -- INPUT.md, REQUEST.md, DECISION.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowstate.engine.context import create_judge_dir, write_task_input
from flowstate.engine.judge import (
    JudgeContext,
    read_judge_decision,
    write_judge_decision,
    write_judge_request,
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
# Tests: write_task_input
# ---------------------------------------------------------------------------


class TestWriteTaskInput:
    def test_write_task_input(self, tmp_path: Path) -> None:
        """Write prompt to INPUT.md and verify file exists with correct contents."""
        task_dir = str(tmp_path / "tasks" / "analyze-1")
        Path(task_dir).mkdir(parents=True)

        prompt = "Analyze the codebase and identify areas for improvement."
        result_path = write_task_input(task_dir, prompt)

        assert result_path.endswith("INPUT.md")
        input_file = Path(result_path)
        assert input_file.exists()
        assert input_file.read_text() == prompt

    def test_write_task_input_overwrites(self, tmp_path: Path) -> None:
        """Writing INPUT.md a second time overwrites the first."""
        task_dir = str(tmp_path)
        write_task_input(task_dir, "first prompt")
        write_task_input(task_dir, "second prompt")

        content = (Path(task_dir) / "INPUT.md").read_text()
        assert content == "second prompt"


# ---------------------------------------------------------------------------
# Tests: create_judge_dir
# ---------------------------------------------------------------------------


class TestCreateJudgeDir:
    def test_create_judge_dir(self, tmp_path: Path) -> None:
        """Creates <run_data_dir>/judge/<source>-<gen>/ and returns the path."""
        run_dir = str(tmp_path / "run-abc")
        result = create_judge_dir(run_dir, "review", 1)

        assert Path(result).exists()
        assert Path(result).is_dir()
        assert result.endswith("review-1")
        # Verify the full path structure
        assert "/judge/" in result

    def test_create_judge_dir_creates_parents(self, tmp_path: Path) -> None:
        """Non-existent run_data_dir and judge dir are created."""
        run_dir = str(tmp_path / "deep" / "nested" / "run-xyz")
        result = create_judge_dir(run_dir, "analyze", 2)

        assert Path(result).exists()
        assert result.endswith("analyze-2")

    def test_create_judge_dir_idempotent(self, tmp_path: Path) -> None:
        """Calling twice with the same args does not raise (exist_ok=True)."""
        run_dir = str(tmp_path / "run-idem")
        result1 = create_judge_dir(run_dir, "test", 1)
        result2 = create_judge_dir(run_dir, "test", 1)

        assert result1 == result2
        assert Path(result1).is_dir()


# ---------------------------------------------------------------------------
# Tests: write_judge_request
# ---------------------------------------------------------------------------


class TestWriteJudgeRequest:
    def test_write_judge_request(self, tmp_path: Path) -> None:
        """Write REQUEST.md and verify all expected fields are present."""
        judge_dir = str(tmp_path / "judge" / "review-1")
        Path(judge_dir).mkdir(parents=True)

        ctx = _make_judge_context()
        result_path = write_judge_request(judge_dir, ctx)

        assert result_path.endswith("REQUEST.md")
        request_file = Path(result_path)
        assert request_file.exists()

        content = request_file.read_text()
        # Verify all expected fields from the judge prompt
        assert "review" in content
        assert "Review the code changes" in content
        assert "Exit Code: 0" in content
        assert "All tests pass." in content
        assert "/workspace/project" in content
        assert "tests pass" in content
        assert "tests fail" in content
        assert "deploy" in content
        assert "fix" in content

    def test_write_judge_request_no_summary(self, tmp_path: Path) -> None:
        """REQUEST.md with no summary includes fallback text."""
        judge_dir = str(tmp_path)
        ctx = _make_judge_context(summary=None)
        write_judge_request(judge_dir, ctx)

        content = (Path(judge_dir) / "REQUEST.md").read_text()
        assert "(No summary was written by the task)" in content


# ---------------------------------------------------------------------------
# Tests: write_judge_decision / read_judge_decision round-trip
# ---------------------------------------------------------------------------


class TestJudgeDecisionRoundTrip:
    def test_write_read_judge_decision(self, tmp_path: Path) -> None:
        """Write DECISION.json and read it back, verifying round-trip."""
        judge_dir = str(tmp_path)

        write_path = write_judge_decision(
            judge_dir,
            decision="deploy",
            reasoning="All tests pass and coverage is good.",
            confidence=0.92,
        )
        assert write_path.endswith("DECISION.json")
        assert Path(write_path).exists()

        # Verify raw JSON structure
        raw = Path(write_path).read_text()
        data = json.loads(raw)
        assert data["decision"] == "deploy"
        assert data["reasoning"] == "All tests pass and coverage is good."
        assert data["confidence"] == 0.92

        # Read back via the function
        decision = read_judge_decision(judge_dir)
        assert decision.target == "deploy"
        assert decision.reasoning == "All tests pass and coverage is good."
        assert decision.confidence == 0.92

    def test_write_read_none_decision(self, tmp_path: Path) -> None:
        """Round-trip with __none__ decision."""
        judge_dir = str(tmp_path)
        write_judge_decision(judge_dir, "__none__", "No condition matches", 0.6)

        decision = read_judge_decision(judge_dir)
        assert decision.target == "__none__"
        assert decision.is_none is True

    def test_write_read_integer_confidence(self, tmp_path: Path) -> None:
        """Confidence of 1 (integer) is read back as 1.0 (float)."""
        judge_dir = str(tmp_path)
        # Write raw JSON with integer confidence
        (Path(judge_dir) / "DECISION.json").write_text(
            json.dumps({"decision": "deploy", "reasoning": "Sure", "confidence": 1})
        )

        decision = read_judge_decision(judge_dir)
        assert decision.confidence == 1.0
        assert isinstance(decision.confidence, float)


# ---------------------------------------------------------------------------
# Tests: read_judge_decision error cases
# ---------------------------------------------------------------------------


class TestReadJudgeDecisionErrors:
    def test_read_judge_decision_missing(self, tmp_path: Path) -> None:
        """FileNotFoundError when DECISION.json does not exist."""
        with pytest.raises(FileNotFoundError, match=r"DECISION\.json not found"):
            read_judge_decision(str(tmp_path))

    def test_read_judge_decision_malformed_json(self, tmp_path: Path) -> None:
        """ValueError when DECISION.json contains invalid JSON."""
        (tmp_path / "DECISION.json").write_text("not valid json {{{")
        with pytest.raises(ValueError, match="invalid JSON"):
            read_judge_decision(str(tmp_path))

    def test_read_judge_decision_not_object(self, tmp_path: Path) -> None:
        """ValueError when DECISION.json is a JSON array instead of object."""
        (tmp_path / "DECISION.json").write_text("[1, 2, 3]")
        with pytest.raises(ValueError, match="must be a JSON object"):
            read_judge_decision(str(tmp_path))

    def test_read_judge_decision_missing_field(self, tmp_path: Path) -> None:
        """ValueError when required fields are missing."""
        (tmp_path / "DECISION.json").write_text(
            json.dumps({"decision": "deploy", "reasoning": "ok"})
        )
        with pytest.raises(ValueError, match="missing required field 'confidence'"):
            read_judge_decision(str(tmp_path))

    def test_read_judge_decision_wrong_type_decision(self, tmp_path: Path) -> None:
        """ValueError when decision is not a string."""
        (tmp_path / "DECISION.json").write_text(
            json.dumps({"decision": 42, "reasoning": "ok", "confidence": 0.9})
        )
        with pytest.raises(ValueError, match="'decision' must be a string"):
            read_judge_decision(str(tmp_path))

    def test_read_judge_decision_wrong_type_confidence(self, tmp_path: Path) -> None:
        """ValueError when confidence is not a number."""
        (tmp_path / "DECISION.json").write_text(
            json.dumps({"decision": "deploy", "reasoning": "ok", "confidence": "high"})
        )
        with pytest.raises(ValueError, match="'confidence' must be a number"):
            read_judge_decision(str(tmp_path))

    def test_read_judge_decision_confidence_out_of_range(self, tmp_path: Path) -> None:
        """ValueError when confidence is outside [0.0, 1.0]."""
        (tmp_path / "DECISION.json").write_text(
            json.dumps({"decision": "deploy", "reasoning": "ok", "confidence": 1.5})
        )
        with pytest.raises(ValueError, match=r"must be between 0\.0 and 1\.0"):
            read_judge_decision(str(tmp_path))

    def test_read_judge_decision_negative_confidence(self, tmp_path: Path) -> None:
        """ValueError when confidence is negative."""
        (tmp_path / "DECISION.json").write_text(
            json.dumps({"decision": "deploy", "reasoning": "ok", "confidence": -0.1})
        )
        with pytest.raises(ValueError, match=r"must be between 0\.0 and 1\.0"):
            read_judge_decision(str(tmp_path))
