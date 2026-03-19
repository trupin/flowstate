# [ENGINE-004] Judge Protocol

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-001
- Blocks: ENGINE-007

## Spec References
- specs.md Section 7.1 — "Judge Prompt Template"
- specs.md Section 7.2 — "Judge Output Schema"
- specs.md Section 7.3 — "Judge Invocation"
- specs.md Section 7.4 — "Judge Failure Handling"
- specs.md Section 6.5 — "Conditional Branching"
- agents/03-engine.md — "Judge Protocol"

## Summary
Implement the judge protocol that evaluates conditional edges after a task completes. When a node has conditional outgoing edges, the engine invokes a judge subprocess — a Claude Code process running in read-only plan mode with the Sonnet model. The judge reads the completed task's SUMMARY.md and workspace state, evaluates which condition matches, and returns a structured decision. This module handles prompt construction from the template in Section 7.1, JSON schema generation with the correct enum values, invocation via the subprocess manager, response parsing, and failure handling (retry once on crash/invalid output, pause on repeated failure or low confidence).

## Acceptance Criteria
- [ ] File `src/flowstate/engine/judge.py` exists and is importable
- [ ] `JudgeProtocol` class is implemented with the following interface:
  - `__init__(self, subprocess_mgr: SubprocessManager)` — accepts the subprocess manager
  - `async evaluate(self, context: JudgeContext) -> JudgeDecision` — runs the full judge evaluation
- [ ] `JudgeContext` dataclass contains all info needed to build the prompt:
  - `node_name: str` — the completed task's node name
  - `task_prompt: str` — the original task prompt
  - `exit_code: int` — task exit code
  - `summary: str | None` — contents of SUMMARY.md (may be None)
  - `task_cwd: str` — the task's working directory
  - `run_id: str` — the flow run ID
  - `outgoing_edges: list[tuple[str, str]]` — list of (condition, target_node_name) pairs
- [ ] `JudgeDecision` dataclass contains:
  - `target: str` — the chosen target node name (or `"__none__"`)
  - `reasoning: str` — the judge's explanation
  - `confidence: float` — confidence score 0.0 to 1.0
  - `is_none: bool` — True if decision is `"__none__"`
  - `is_low_confidence: bool` — True if confidence < 0.5
- [ ] The judge prompt matches the template from specs.md Section 7.1 exactly
- [ ] The JSON schema is generated with the correct enum values (target node names + `"__none__"`)
- [ ] On subprocess crash or invalid output: retry once automatically
- [ ] On second failure (retry also fails): raise `JudgePauseError` (signals executor to pause)
- [ ] On `"__none__"` decision: return `JudgeDecision` with `is_none=True`
- [ ] On confidence < 0.5: return `JudgeDecision` with `is_low_confidence=True`
- [ ] `JudgePauseError` includes the failure reason for the pause event
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/judge.py` — judge protocol implementation
- `tests/engine/test_judge.py` — tests

### Key Implementation Details

#### Data Types

```python
from dataclasses import dataclass


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
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
```

#### Prompt Construction

Build the prompt exactly matching specs.md Section 7.1:

```python
JUDGE_PROMPT_TEMPLATE = """\
You are a routing judge for the Flowstate orchestration system.

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
    transitions = "\n".join(
        f'- "{condition}" → transitions to: {target}'
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
```

#### JSON Schema Generation

```python
def build_judge_schema(outgoing_edges: list[tuple[str, str]]) -> dict:
    """Build the JSON schema for the judge's output.

    The enum includes all target node names plus "__none__".
    """
    targets = [target for _, target in outgoing_edges]
    # Deduplicate while preserving order (a node could be target of multiple conditions)
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
```

#### Judge Evaluation with Retry

```python
class JudgeProtocol:
    CONFIDENCE_THRESHOLD = 0.5

    def __init__(self, subprocess_mgr: SubprocessManager) -> None:
        self._subprocess_mgr = subprocess_mgr

    async def evaluate(self, context: JudgeContext) -> JudgeDecision:
        """Run judge evaluation. Retries once on failure. Raises JudgePauseError on repeated failure."""
        prompt = build_judge_prompt(context)

        # First attempt
        try:
            result = await self._subprocess_mgr.run_judge(prompt, context.task_cwd)
            return self._parse_result(result, context)
        except (JudgeError, KeyError, ValueError) as first_error:
            pass

        # Retry (second attempt)
        try:
            result = await self._subprocess_mgr.run_judge(prompt, context.task_cwd)
            return self._parse_result(result, context)
        except (JudgeError, KeyError, ValueError) as second_error:
            raise JudgePauseError(
                f"Judge failed after retry: {second_error}"
            ) from second_error

    def _parse_result(self, result: JudgeResult, context: JudgeContext) -> JudgeDecision:
        """Parse and validate the JudgeResult from the subprocess manager."""
        valid_targets = {target for _, target in context.outgoing_edges}
        valid_targets.add("__none__")

        if result.decision not in valid_targets:
            raise ValueError(
                f"Judge returned invalid target '{result.decision}'. "
                f"Valid targets: {valid_targets}"
            )

        if not (0.0 <= result.confidence <= 1.0):
            raise ValueError(
                f"Judge confidence {result.confidence} is outside [0, 1] range"
            )

        return JudgeDecision(
            target=result.decision,
            reasoning=result.reasoning,
            confidence=result.confidence,
        )
```

### Edge Cases
- **Summary is None**: The prompt includes "(No summary was written by the task)" placeholder text. The judge should still be able to make a decision by inspecting the workspace.
- **Duplicate target nodes**: Multiple conditions can point to the same target. The schema enum deduplicates targets.
- **Decision is a valid target but not one of the conditions' targets**: The schema constrains the enum, so the subprocess manager should reject this. If it somehow passes through, `_parse_result` validates against the known target set.
- **Confidence exactly 0.5**: Not low confidence (threshold is `< 0.5`, not `<= 0.5`).
- **First attempt succeeds but returns `__none__`**: This is NOT a failure — return the decision normally. The executor decides to pause.
- **First attempt succeeds but with low confidence**: Also not a failure — return the decision. The executor decides to pause.
- **Retry also returns `__none__` or low confidence**: These are valid outcomes, not errors. Only subprocess crashes and parse failures trigger retries.
- **Judge prompt with very long summary**: The full SUMMARY.md is included. No truncation — the judge model (Sonnet) handles long inputs.

## Testing Strategy

Create `tests/engine/test_judge.py`:

1. **test_build_judge_prompt** — Build a prompt with known context. Verify all sections are present: "Completed Task" with node name/prompt/exit code, "Task Summary" with summary text, "Task Working Directory" with cwd, "Available Transitions" with all edges listed.

2. **test_build_judge_prompt_no_summary** — Context with summary=None. Verify "(No summary was written by the task)" appears.

3. **test_build_judge_prompt_multiple_edges** — Context with 3 outgoing edges. Verify all 3 transitions are listed.

4. **test_build_judge_schema** — Build schema for 2 edges with different targets. Verify enum has both targets + "__none__".

5. **test_build_judge_schema_deduplicates** — Two edges pointing to the same target. Verify enum has the target once + "__none__".

6. **test_evaluate_happy_path** — Mock subprocess_mgr.run_judge to return valid JudgeResult. Verify JudgeDecision has correct target, reasoning, confidence.

7. **test_evaluate_none_decision** — Mock returns decision="__none__". Verify `is_none` is True.

8. **test_evaluate_low_confidence** — Mock returns confidence=0.3. Verify `is_low_confidence` is True.

9. **test_evaluate_confidence_exactly_0_5** — Mock returns confidence=0.5. Verify `is_low_confidence` is False.

10. **test_evaluate_retry_on_first_failure** — Mock run_judge to raise JudgeError on first call, return valid result on second. Verify the valid result is returned (retry succeeded).

11. **test_evaluate_pause_on_double_failure** — Mock run_judge to raise JudgeError on both calls. Verify JudgePauseError is raised.

12. **test_evaluate_retry_on_invalid_target** — First call returns an invalid target (ValueError in _parse_result). Second call returns valid. Verify retry works.

13. **test_evaluate_invalid_confidence_range** — Mock returns confidence=1.5. Verify ValueError triggers retry.

14. **test_evaluate_pause_reason_message** — On double failure, verify the JudgePauseError.reason contains useful information.

Mock the `SubprocessManager` entirely — use `unittest.mock.AsyncMock` for `run_judge`. Never call real Claude Code in tests.
