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

from dataclasses import dataclass

from flowstate.engine.subprocess_mgr import JudgeError, JudgeResult, SubprocessManager


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

    Retries once on subprocess crash or invalid output.  Raises
    JudgePauseError if both attempts fail.
    """

    CONFIDENCE_THRESHOLD = 0.5

    def __init__(self, subprocess_mgr: SubprocessManager) -> None:
        self._subprocess_mgr = subprocess_mgr

    async def evaluate(self, context: JudgeContext) -> JudgeDecision:
        """Run judge evaluation with automatic retry on failure.

        Retries once on subprocess crash (JudgeError) or invalid output
        (KeyError / ValueError).  Raises JudgePauseError if the retry
        also fails.
        """
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
