"""Context assembly -- task directory setup, prompt construction, and template expansion.

Prepares everything needed before launching a Claude Code subprocess:
- Creating task directories under ~/.flowstate/runs/<run-id>/tasks/
- Constructing prompts based on context mode (handoff, session, none, join)
- Expanding {{param}} template variables
- Resolving the effective context mode from edge/flow configuration
- Reading SUMMARY.md files from predecessor tasks
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flowstate.dsl.ast import ContextMode, Edge, Flow, Node


class CwdResolutionError(Exception):
    """Raised when neither node nor flow specifies a working directory."""


def create_task_dir(run_data_dir: str, node_name: str, generation: int) -> str:
    """Create the task directory and return its absolute path.

    Creates: <run_data_dir>/tasks/<name>-<gen>/
    Also creates <run_data_dir>/ if it does not exist.
    """
    run_path = Path(run_data_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    tasks_path = run_path / "tasks"
    tasks_path.mkdir(exist_ok=True)

    task_dir = tasks_path / f"{node_name}-{generation}"
    task_dir.mkdir(exist_ok=True)

    return str(task_dir.resolve())


def build_prompt_handoff(
    node: Node,
    task_dir: str,
    cwd: str,
    predecessor_summary: str | None,
) -> str:
    """Build the full prompt for handoff mode (fresh session with predecessor context).

    If predecessor_summary is None, a fallback message is included instead.
    """
    if predecessor_summary is None:
        context_section = "(No summary available from predecessor task)"
    else:
        context_section = predecessor_summary

    return (
        "You are executing a task in a Flowstate workflow.\n"
        f"[flowstate:node={node.name}]\n"
        "\n"
        "## Context from previous task\n"
        f"{context_section}\n"
        "\n"
        "## Your task\n"
        f"{node.prompt}\n"
        "\n"
        "## Working directory\n"
        f"Your working directory is: {cwd}\n"
        "Make all code changes and deliverable output in this directory.\n"
        "\n"
        "## Task coordination directory\n"
        f"Write coordination files to {task_dir}/.\n"
        "Do NOT write project deliverables here — this is for inter-agent communication only.\n"
        f"When you are done, you MUST write a SUMMARY.md to {task_dir}/SUMMARY.md describing:\n"
        "- What you did\n"
        "- What changed\n"
        "- The outcome / current state"
    )


def build_prompt_session(node: Node, task_dir: str) -> str:
    """Build the shorter session-mode prompt (resumed session).

    Does not include context or working directory sections since the full
    conversation context is already present in the resumed session.
    """
    return (
        f"[flowstate:node={node.name}]\n"
        f"## Next task: {node.name}\n"
        f"{node.prompt}\n"
        "\n"
        f"When you are done, write a SUMMARY.md to {task_dir}/SUMMARY.md\n"
        "describing what you did and the outcome."
    )


def build_prompt_none(node: Node, task_dir: str, cwd: str) -> str:
    """Build prompt with no upstream context (fresh session, self-contained task)."""
    return (
        "You are executing a task in a Flowstate workflow.\n"
        f"[flowstate:node={node.name}]\n"
        "\n"
        "## Your task\n"
        f"{node.prompt}\n"
        "\n"
        "## Working directory\n"
        f"Your working directory is: {cwd}\n"
        "Make all code changes and deliverable output in this directory.\n"
        "\n"
        "## Task coordination directory\n"
        f"Write coordination files to {task_dir}/.\n"
        "Do NOT write project deliverables here — this is for inter-agent communication only.\n"
        f"When you are done, you MUST write a SUMMARY.md to {task_dir}/SUMMARY.md describing:\n"
        "- What you did\n"
        "- What changed\n"
        "- The outcome / current state"
    )


def build_prompt_join(
    node: Node,
    task_dir: str,
    cwd: str,
    member_summaries: dict[str, str | None],
) -> str:
    """Build prompt for join nodes with aggregated fork member summaries.

    member_summaries maps member node names to their SUMMARY.md contents
    (or None if the summary is missing).
    """
    members_section_parts: list[str] = []
    for member_name, summary in member_summaries.items():
        summary_text = "(No summary available)" if summary is None else summary
        members_section_parts.append(f"### {member_name}\n{summary_text}")

    members_section = "\n\n".join(members_section_parts)

    return (
        "You are executing a task in a Flowstate workflow.\n"
        f"[flowstate:node={node.name}]\n"
        "\n"
        "## Context from parallel tasks\n"
        "\n"
        f"{members_section}\n"
        "\n"
        "## Your task\n"
        f"{node.prompt}\n"
        "\n"
        "## Working directory\n"
        f"Your working directory is: {cwd}\n"
        "Make all code changes and deliverable output in this directory.\n"
        "\n"
        "## Task coordination directory\n"
        f"Write coordination files to {task_dir}/.\n"
        "Do NOT write project deliverables here — this is for inter-agent communication only.\n"
        f"When you are done, you MUST write a SUMMARY.md to {task_dir}/SUMMARY.md describing:\n"
        "- What you did\n"
        "- What changed\n"
        "- The outcome / current state"
    )


def expand_templates(text: str, params: dict[str, str | float | bool]) -> str:
    """Replace {{param_name}} with actual parameter values.

    Handles string, number, and bool types by converting to string.
    Unmatched template variables are left as-is (the type checker
    should have caught missing params, but defensive coding).
    Whitespace inside braces is tolerated: {{ repo }} works like {{repo}}.
    """

    def replacer(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name in params:
            return str(params[name])
        return match.group(0)  # leave unmatched as-is

    return re.sub(r"\{\{(\s*\w+\s*)\}\}", replacer, text)


def get_context_mode(edge: Edge, flow: Flow) -> ContextMode:
    """Resolve the effective context mode for an edge.

    Edge-level context override takes precedence over flow-level default.
    """
    if edge.config.context is not None:
        return edge.config.context
    return flow.context


def resolve_cwd(node: Node, flow: Flow) -> str:
    """Resolve the working directory for a task.

    Priority: node.cwd > flow.workspace > error.
    """
    if node.cwd is not None:
        return node.cwd
    if flow.workspace is not None:
        return flow.workspace
    raise CwdResolutionError(
        f"No working directory for node '{node.name}': "
        f"neither node.cwd nor flow.workspace is set"
    )


def read_summary(task_dir: str) -> str | None:
    """Read SUMMARY.md from a task directory.

    Returns the file contents as a string, or None if the file does not exist.
    """
    summary_path = Path(task_dir) / "SUMMARY.md"
    if summary_path.exists():
        return summary_path.read_text()
    return None


def write_task_input(task_dir: str, prompt: str) -> str:
    """Write the assembled task prompt to INPUT.md in the task directory.

    Returns the absolute path to the written file.
    """
    input_path = Path(task_dir) / "INPUT.md"
    input_path.write_text(prompt)
    return str(input_path)


def build_routing_instructions(
    task_dir: str,
    outgoing_edges: list[tuple[str, str]],
) -> str:
    """Build self-report routing instructions to append to a task prompt.

    When the judge is disabled, the task agent itself decides which transition
    to take by writing a DECISION.json file.

    Args:
        task_dir: Path to the task's working directory.
        outgoing_edges: List of (condition, target_node_name) pairs.
    """
    transitions = "\n".join(
        f'- "{condition}" → transitions to: {target}' for condition, target in outgoing_edges
    )

    return (
        "\n\n## Routing Decision\n"
        "After completing your task, you must decide which transition to take.\n"
        "\n"
        "### Available Transitions\n"
        f"{transitions}\n"
        '\nIf no condition clearly matches, use "__none__".\n'
        "\n"
        "### Instructions\n"
        f"Write a JSON file to {task_dir}/DECISION.json with this format:\n"
        "```json\n"
        '{"decision": "<target_node_name>", "reasoning": "<brief explanation>", '
        '"confidence": <float 0.0 to 1.0>}\n'
        "```\n"
        "You MUST write this file before completing your task."
    )


def create_judge_dir(run_data_dir: str, source_node: str, generation: int) -> str:
    """Create judge directory: <run_data_dir>/judge/<source>-<gen>/.

    Returns the absolute path to the created directory.
    """
    judge_dir = Path(run_data_dir) / "judge" / f"{source_node}-{generation}"
    judge_dir.mkdir(parents=True, exist_ok=True)
    return str(judge_dir)
