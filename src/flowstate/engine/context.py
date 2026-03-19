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
        "\n"
        "## Task directory\n"
        f"Write your working notes and scratch files to {task_dir}/.\n"
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
        "\n"
        "## Task directory\n"
        f"Write your working notes and scratch files to {task_dir}/.\n"
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
        "\n"
        "## Task directory\n"
        f"Write your working notes and scratch files to {task_dir}/.\n"
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


def create_judge_dir(run_data_dir: str, source_node: str, generation: int) -> str:
    """Create judge directory: <run_data_dir>/judge/<source>-<gen>/.

    Returns the absolute path to the created directory.
    """
    judge_dir = Path(run_data_dir) / "judge" / f"{source_node}-{generation}"
    judge_dir.mkdir(parents=True, exist_ok=True)
    return str(judge_dir)


def serialize_flow_graph(flow: Flow) -> str:
    """Serialize a Flow AST into a readable text representation for the orchestrator.

    Lists all nodes (with their type and prompt summary) and all edges
    (with type, conditions, and targets).
    """
    lines: list[str] = []

    lines.append("## Nodes")
    for name, node in flow.nodes.items():
        prompt_summary = node.prompt
        if len(prompt_summary) > 120:
            prompt_summary = prompt_summary[:117] + "..."
        lines.append(f"- **{name}** (type: {node.node_type}): {prompt_summary}")

    lines.append("")
    lines.append("## Edges")
    for edge in flow.edges:
        if edge.edge_type == "unconditional":
            lines.append(f"- {edge.source} -> {edge.target}")
        elif edge.edge_type == "conditional":
            lines.append(f'- {edge.source} -> {edge.target} [condition: "{edge.condition}"]')
        elif edge.edge_type == "fork":
            targets = ", ".join(edge.fork_targets) if edge.fork_targets else ""
            lines.append(f"- {edge.source} -> fork({targets})")
        elif edge.edge_type == "join":
            sources = ", ".join(edge.join_sources) if edge.join_sources else ""
            lines.append(f"- join({sources}) -> {edge.target}")

    return "\n".join(lines)


def build_orchestrator_system_prompt(flow: Flow, run_data_dir: str, cwd: str) -> str:
    """Build the orchestrator's system prompt.

    Includes: identity, flow graph, task execution protocol, judge evaluation
    protocol, DECISION.json format, fork handling, and file paths.
    """
    flow_graph = serialize_flow_graph(flow)

    return (
        "# Flowstate Orchestrator Agent\n"
        "\n"
        "You are a Flowstate orchestrator agent managing a flow run. "
        "You coordinate task execution and judge transitions for the "
        f'flow "{flow.name}".\n'
        "\n"
        "# Flow Graph\n"
        "\n"
        f"{flow_graph}\n"
        "\n"
        "# Task Execution Protocol\n"
        "\n"
        "For each task node you must execute:\n"
        "1. Read the task's INPUT.md file to get the full assembled prompt.\n"
        '2. Spawn a subagent using the Agent tool with `model: "opus"` to execute the task.\n'
        "3. After the subagent completes, ensure a SUMMARY.md file exists in the task directory.\n"
        "\n"
        "# Judge Evaluation Protocol\n"
        "\n"
        "When a completed task has conditional outgoing edges, you act as the judge:\n"
        "1. Read the REQUEST.md file in the judge directory for full evaluation context.\n"
        "2. Evaluate which transition condition best matches the task outcome.\n"
        "3. Write your decision to DECISION.json in the same judge directory.\n"
        "\n"
        "# DECISION.json Format\n"
        "\n"
        "```json\n"
        '{"decision": "<target_node_name_or___none__>", '
        '"reasoning": "<brief explanation>", '
        '"confidence": <float 0.0 to 1.0>}\n'
        "```\n"
        "\n"
        "- `decision`: The target node name to transition to, or `__none__` if no condition matches.\n"
        "- `reasoning`: A brief explanation of why this transition was chosen.\n"
        "- `confidence`: A float between 0.0 and 1.0 indicating your confidence.\n"
        "\n"
        "# Fork Handling\n"
        "\n"
        "When a fork edge is encountered, use parallel Agent tool calls to execute "
        "all fork branches concurrently. Wait for all branches to complete before "
        "proceeding to the join node.\n"
        "\n"
        "# File Paths\n"
        "\n"
        f"- Run data directory: {run_data_dir}\n"
        f"- Task directory pattern: {run_data_dir}/tasks/<node_name>-<generation>/\n"
        f"- Judge directory pattern: {run_data_dir}/judge/<source_node>-<generation>/\n"
        f"- Working directory: {cwd}\n"
    )
