"""Judge protocol -- evaluates conditional edges after task completion.

When a node has conditional outgoing edges, the engine invokes a judge
subprocess (a Claude Code process in read-only plan mode with the Sonnet model).
The judge reads the completed task's SUMMARY.md and workspace state, evaluates
which condition matches, and returns a structured decision.

This module handles prompt construction, JSON schema generation, subprocess
invocation via the SubprocessManager, response parsing, and failure handling
(retry once on crash/invalid output, pause on repeated failure or low confidence).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from flowstate.engine.subprocess_mgr import JudgeError, JudgeResult, SubprocessManager

if TYPE_CHECKING:
    from flowstate.engine.orchestrator import OrchestratorManager, OrchestratorSession


@dataclass
class JudgeContext:
    """All information needed to build a judge prompt."""

    node_name: str
    task_prompt: str
    exit_code: int
    summary: str | None
    task_cwd: str
    run_id: str
    outgoing_edges: list[tuple[str, str]]  # (condition, target_node_name)
    skip_permissions: bool = False


@dataclass
class JudgeDecision:
    """The judge's structured decision."""

    target: str
    reasoning: str
    confidence: float

    @property
    def is_none(self) -> bool:
        return self.target == "__none__"

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < 0.5


class JudgePauseError(Exception):
    """Raised when the judge fails and the flow should be paused."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


JUDGE_PROMPT_TEMPLATE = """\
You are a routing judge for the Flowstate orchestration system.
[flowstate:node={node_name}]

## Completed Task
- Name: {node_name}
- Prompt: {task_prompt}
- Exit Code: {exit_code}

## Task Summary
The following summary was written by the task agent:

---
{summary}
---

## Task Working Directory
The task ran in: {task_cwd}
You have read-only access. You may inspect files beyond the summary
if needed for your decision.

## Available Transitions
{transitions}

## Instructions
Based on the task summary and workspace state, determine which transition
condition best matches the current state of the work. You MUST select
exactly one target. If no condition clearly matches, select "__none__"."""


def build_judge_prompt(ctx: JudgeContext) -> str:
    """Construct the full judge prompt from a JudgeContext."""
    transitions = "\n".join(
        f'- "{condition}" \u2192 transitions to: {target}'
        for condition, target in ctx.outgoing_edges
    )
    summary = ctx.summary if ctx.summary else "(No summary was written by the task)"

    return JUDGE_PROMPT_TEMPLATE.format(
        node_name=ctx.node_name,
        task_prompt=ctx.task_prompt,
        exit_code=ctx.exit_code,
        summary=summary,
        task_cwd=ctx.task_cwd,
        transitions=transitions,
    )


def build_judge_schema(outgoing_edges: list[tuple[str, str]]) -> dict[str, object]:
    """Build the JSON schema for the judge's output.

    The enum includes all target node names plus "__none__".
    Duplicate targets are deduplicated while preserving order.
    """
    targets = [target for _, target in outgoing_edges]
    seen: set[str] = set()
    unique_targets: list[str] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            unique_targets.append(t)
    unique_targets.append("__none__")

    return {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": unique_targets,
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of why this transition was chosen",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How confident the judge is in this decision",
            },
        },
        "required": ["decision", "reasoning", "confidence"],
    }


class JudgeProtocol:
    """Evaluates conditional edges by invoking a judge subprocess.

    Optionally routes evaluation through an orchestrator session when one
    is available, falling back to a direct judge subprocess on failure.

    Retries once on subprocess crash or invalid output.  Raises
    JudgePauseError if both attempts fail.
    """

    CONFIDENCE_THRESHOLD = 0.5

    def __init__(
        self,
        subprocess_mgr: SubprocessManager,
        orchestrator_mgr: OrchestratorManager | None = None,
    ) -> None:
        self._subprocess_mgr = subprocess_mgr
        self._orchestrator_mgr = orchestrator_mgr

    async def evaluate(
        self,
        context: JudgeContext,
        *,
        orchestrator_session: OrchestratorSession | None = None,
        run_data_dir: str | None = None,
    ) -> JudgeDecision:
        """Run judge evaluation with automatic retry on failure.

        If an orchestrator session is provided, tries orchestrator path first.
        Falls back to direct subprocess on failure.

        Retries once on subprocess crash (JudgeError) or invalid output
        (KeyError / ValueError).  Raises JudgePauseError if the retry
        also fails.
        """
        # Try orchestrator path first
        if orchestrator_session is not None and run_data_dir is not None:
            try:
                return await self.evaluate_via_orchestrator(
                    context,
                    orchestrator_session,
                    run_data_dir,
                )
            except Exception:
                pass  # Fall through to direct subprocess

        # Direct subprocess path (existing logic)
        prompt = build_judge_prompt(context)

        # First attempt
        try:
            result = await self._subprocess_mgr.run_judge(
                prompt, context.task_cwd, skip_permissions=context.skip_permissions
            )
            return self._parse_result(result, context)
        except (JudgeError, KeyError, ValueError):
            pass

        # Retry (second attempt)
        try:
            result = await self._subprocess_mgr.run_judge(
                prompt, context.task_cwd, skip_permissions=context.skip_permissions
            )
            return self._parse_result(result, context)
        except (JudgeError, KeyError, ValueError) as second_error:
            raise JudgePauseError(f"Judge failed after retry: {second_error}") from second_error

    async def evaluate_via_orchestrator(
        self,
        context: JudgeContext,
        session: OrchestratorSession,
        run_data_dir: str,
    ) -> JudgeDecision:
        """Evaluate conditional edges via the orchestrator session.

        Writes REQUEST.md, resumes the orchestrator with judge instruction,
        reads DECISION.json after completion.
        """
        from flowstate.engine.context import create_judge_dir
        from flowstate.engine.orchestrator import build_judge_instruction

        # Create judge directory and write request
        judge_dir = create_judge_dir(run_data_dir, context.node_name, 1)
        request_path = write_judge_request(judge_dir, context)
        decision_path = str(Path(judge_dir) / "DECISION.json")

        targets = [target for _, target in context.outgoing_edges]
        instruction = build_judge_instruction(
            context.node_name,
            request_path,
            decision_path,
            targets,
        )

        # Resume orchestrator
        stream = self._subprocess_mgr.run_task_resume(
            instruction,
            context.task_cwd,
            session.session_id,
            skip_permissions=context.skip_permissions,
        )
        async for _event in stream:
            pass  # Wait for completion

        # Read decision from file
        return read_judge_decision(judge_dir)

    def _parse_result(self, result: JudgeResult, context: JudgeContext) -> JudgeDecision:
        """Parse and validate the JudgeResult from the subprocess manager.

        Raises ValueError if the decision target is invalid or the
        confidence is outside [0, 1].
        """
        valid_targets = {target for _, target in context.outgoing_edges}
        valid_targets.add("__none__")

        if result.decision not in valid_targets:
            raise ValueError(
                f"Judge returned invalid target '{result.decision}'. "
                f"Valid targets: {valid_targets}"
            )

        if not (0.0 <= result.confidence <= 1.0):
            raise ValueError(f"Judge confidence {result.confidence} is outside [0, 1] range")

        return JudgeDecision(
            target=result.decision,
            reasoning=result.reasoning,
            confidence=result.confidence,
        )


def write_judge_request(judge_dir: str, context: JudgeContext) -> str:
    """Write REQUEST.md with judge evaluation context.

    Uses the same content as build_judge_prompt() to produce the file.
    Returns the absolute path to the written file.
    """
    request_path = Path(judge_dir) / "REQUEST.md"
    request_path.write_text(build_judge_prompt(context))
    return str(request_path)


def write_judge_decision(judge_dir: str, decision: str, reasoning: str, confidence: float) -> str:
    """Write DECISION.json with structured judge decision.

    Returns the absolute path to the written file.
    """
    decision_path = Path(judge_dir) / "DECISION.json"
    data = {
        "decision": decision,
        "reasoning": reasoning,
        "confidence": confidence,
    }
    decision_path.write_text(json.dumps(data, indent=2))
    return str(decision_path)


def read_judge_decision(judge_dir: str) -> JudgeDecision:
    """Read and parse DECISION.json from judge directory.

    Returns a JudgeDecision with the parsed fields.
    Raises FileNotFoundError if DECISION.json does not exist.
    Raises ValueError if the JSON is malformed or fields are invalid.
    """
    decision_path = Path(judge_dir) / "DECISION.json"
    if not decision_path.exists():
        raise FileNotFoundError(f"DECISION.json not found in {judge_dir}")

    raw = decision_path.read_text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"DECISION.json contains invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"DECISION.json must be a JSON object, got {type(data).__name__}")

    # Validate required fields
    if "decision" not in data:
        raise ValueError("DECISION.json missing required field 'decision'")
    if "reasoning" not in data:
        raise ValueError("DECISION.json missing required field 'reasoning'")
    if "confidence" not in data:
        raise ValueError("DECISION.json missing required field 'confidence'")

    decision_val = data["decision"]
    reasoning_val = data["reasoning"]
    confidence_val = data["confidence"]

    if not isinstance(decision_val, str):
        raise ValueError(f"'decision' must be a string, got {type(decision_val).__name__}")
    if not isinstance(reasoning_val, str):
        raise ValueError(f"'reasoning' must be a string, got {type(reasoning_val).__name__}")
    if not isinstance(confidence_val, int | float):
        raise ValueError(f"'confidence' must be a number, got {type(confidence_val).__name__}")

    confidence_float = float(confidence_val)
    if not (0.0 <= confidence_float <= 1.0):
        raise ValueError(f"'confidence' must be between 0.0 and 1.0, got {confidence_float}")

    return JudgeDecision(
        target=decision_val,
        reasoning=reasoning_val,
        confidence=confidence_float,
    )
