"""Context assembly -- prompt construction and template expansion.

Prepares everything needed before launching a Claude Code subprocess:
- Constructing prompts based on context mode (handoff, session, none, join)
- Expanding {{param}} template variables
- Resolving the effective context mode from edge/flow configuration
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flowstate.dsl.ast import ContextMode, Edge, Flow, Node


class CwdResolutionError(Exception):
    """Raised when neither node nor flow specifies a working directory."""


def _build_directory_sections(cwd: str, *, lumon: bool = False) -> str:
    """Build the shared 'Working directory' and 'Task coordination' prompt sections.

    When lumon=True, uses flowstate.submit_summary() instead of curl commands.
    """
    if lumon:
        return (
            "## Working directory\n"
            f"Your working directory is: {cwd}\n"
            "Make all code changes and deliverable output in this directory.\n"
            "\n"
            "## Task coordination\n"
            "When you are done, you MUST submit a summary of your work using "
            "the flowstate plugin:\n"
            "```\n"
            'flowstate.submit_summary("Your summary here: what you did, what changed, '
            'the outcome")\n'
            "```\n"
            "Describe: what you did, what changed, the outcome / current state."
        )
    return (
        "## Working directory\n"
        f"Your working directory is: {cwd}\n"
        "Make all code changes and deliverable output in this directory.\n"
        "\n"
        "## Task coordination\n"
        "When you are done, you MUST submit a summary of your work:\n"
        "```bash\n"
        "curl -s -X POST $FLOWSTATE_SERVER_URL/api/runs/$FLOWSTATE_RUN_ID"
        "/tasks/$FLOWSTATE_TASK_ID/artifacts/summary \\\n"
        '  -H "Content-Type: text/markdown" \\\n'
        "  -d 'Your summary here: what you did, what changed, the outcome'\n"
        "```\n"
        "Describe: what you did, what changed, the outcome / current state."
    )


def build_prompt_handoff(
    node: Node,
    cwd: str,
    predecessor_summary: str | None,
    *,
    lumon: bool = False,
) -> str:
    """Build the full prompt for handoff mode (fresh session with predecessor context).

    If predecessor_summary is None, a fallback message is included instead.
    When lumon=True, artifact submission instructions use flowstate.submit_*() calls.
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
        "\n" + _build_directory_sections(cwd, lumon=lumon)
    )


def build_prompt_session(node: Node, *, lumon: bool = False) -> str:
    """Build the shorter session-mode prompt (resumed session).

    Does not include context or working directory sections since the full
    conversation context is already present in the resumed session.
    When lumon=True, artifact submission instructions use flowstate.submit_*() calls.
    """
    if lumon:
        return (
            f"[flowstate:node={node.name}]\n"
            f"## Next task: {node.name}\n"
            f"{node.prompt}\n"
            "\n"
            "When you are done, submit a summary of your work:\n"
            "```\n"
            'flowstate.submit_summary("Your summary here: what you did, what changed, '
            'the outcome")\n'
            "```"
        )
    return (
        f"[flowstate:node={node.name}]\n"
        f"## Next task: {node.name}\n"
        f"{node.prompt}\n"
        "\n"
        "When you are done, submit a summary of your work:\n"
        "```bash\n"
        "curl -s -X POST $FLOWSTATE_SERVER_URL/api/runs/$FLOWSTATE_RUN_ID"
        "/tasks/$FLOWSTATE_TASK_ID/artifacts/summary \\\n"
        '  -H "Content-Type: text/markdown" \\\n'
        "  -d 'Your summary here: what you did, what changed, the outcome'\n"
        "```"
    )


def build_prompt_none(node: Node, cwd: str, *, lumon: bool = False) -> str:
    """Build prompt with no upstream context (fresh session, self-contained task).

    When lumon=True, artifact submission instructions use flowstate.submit_*() calls.
    """
    return (
        "You are executing a task in a Flowstate workflow.\n"
        f"[flowstate:node={node.name}]\n"
        "\n"
        "## Your task\n"
        f"{node.prompt}\n"
        "\n" + _build_directory_sections(cwd, lumon=lumon)
    )


def build_prompt_join(
    node: Node,
    cwd: str,
    member_summaries: dict[str, str | None],
    *,
    lumon: bool = False,
) -> str:
    """Build prompt for join nodes with aggregated fork member summaries.

    member_summaries maps member node names to their SUMMARY.md contents
    (or None if the summary is missing).
    When lumon=True, artifact submission instructions use flowstate.submit_*() calls.
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
        "\n" + _build_directory_sections(cwd, lumon=lumon)
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
        f"No working directory for node '{node.name}': neither node.cwd nor flow.workspace is set"
    )


def build_task_management_instructions(
    server_base_url: str,
    run_id: str,
    task_execution_id: str,
    predecessor_task_execution_id: str | None = None,
    *,
    lumon: bool = False,
) -> str:
    """Build prompt section with subtask management instructions.

    When lumon=True, uses flowstate.create_subtask() etc. instead of curl.
    When lumon=False, provides curl examples for the REST API.

    Args:
        server_base_url: The base URL of the Flowstate server (e.g. "http://127.0.0.1:8080").
        run_id: The current flow run ID.
        task_execution_id: The current task execution ID.
        predecessor_task_execution_id: Optional predecessor task execution ID for handoff mode.
        lumon: If True, use Lumon flowstate plugin commands instead of curl.
    """
    lines = [
        "\n\n## Task Management",
        "You have a subtask management system. Break your work into subtasks "
        "and track each one through its full lifecycle: create → `in_progress` → `done`.",
        "",
        "For every subtask:",
        "1. **Create** it before you start the work.",
        "2. **Mark it `in_progress`** when you begin working on it.",
        "3. **Mark it `done`** when the work is complete.",
    ]

    if lumon:
        lines.extend(
            [
                "",
                "### Create a subtask",
                "```",
                "lumon --working-dir sandbox 'return flowstate.create_subtask(\"your subtask title\")'",
                "```",
                "This returns the subtask ID.",
                "",
                "### Update a subtask",
                "```",
                'lumon --working-dir sandbox \'return flowstate.update_subtask("SUBTASK_ID", "in_progress")\'',
                "```",
                "Use `in_progress` when starting, `done` when complete.",
                "",
                "### List your subtasks",
                "```",
                "lumon --working-dir sandbox 'return flowstate.list_subtasks()'",
                "```",
            ]
        )
    else:
        base = server_base_url.rstrip("/")
        task_url = f"{base}/api/runs/{run_id}/tasks/{task_execution_id}/subtasks"
        lines.extend(
            [
                "",
                "### Create a subtask",
                f"curl -s -X POST {task_url} \\",
                '  -H "Content-Type: application/json" \\',
                """  -d '{"title": "your subtask title"}'""",
                "",
                "### Update a subtask",
                f"curl -s -X PATCH {task_url}/{{subtask_id}} \\",
                '  -H "Content-Type: application/json" \\',
                """  -d '{"status": "in_progress"}'  # or "done\"""",
                "",
                "### List your subtasks",
                f"curl -s {task_url}",
            ]
        )

    if predecessor_task_execution_id is not None and not lumon:
        base = server_base_url.rstrip("/")
        pred_url = f"{base}/api/runs/{run_id}/tasks/{predecessor_task_execution_id}/subtasks"
        lines.extend(
            [
                "",
                "### Query predecessor's subtasks",
                f"curl -s {pred_url}",
            ]
        )

    lines.extend(
        [
            "",
            "### Before you exit",
            "Before finishing your work, list your subtasks and confirm every one "
            "is marked `done`. Update any that are not `done`.",
            "",
            "Note: If a subtask API call fails, continue your main work — "
            "do not retry or debug the API. But always attempt to update subtask status.",
        ]
    )

    return "\n".join(lines)


def build_cross_flow_instructions(
    target_flow_names: list[str],
    *,
    lumon: bool = False,
) -> str:
    """Build prompt section instructing the agent to produce cross-flow output.

    When a node has outgoing FILE or AWAIT edges, the agent should POST an
    OUTPUT.json to the Flowstate API so its output can be forwarded to the target flows.

    When lumon=True, uses flowstate.submit_output() instead of curl commands.

    Args:
        target_flow_names: Names of the target flows referenced by FILE/AWAIT edges.
        lumon: Whether to use Lumon plugin instructions instead of curl.
    """
    targets = ", ".join(target_flow_names)
    bullets = "\n".join(f"- {name}" for name in target_flow_names)
    if lumon:
        return (
            "\n\n## Cross-flow output\n"
            f"This task will file tasks to other flows: {targets}.\n"
            f"{bullets}\n"
            "Submit your structured output using the flowstate plugin:\n"
            "```\n"
            """flowstate.submit_output('{"key": "value", ...}')\n"""
            "```\n"
            "These values will be passed as input parameters to the target flow(s)."
        )
    return (
        "\n\n## Cross-flow output\n"
        f"This task will file tasks to other flows: {targets}.\n"
        f"{bullets}\n"
        "Submit your structured output via the API:\n"
        "```bash\n"
        "curl -s -X POST $FLOWSTATE_SERVER_URL/api/runs/$FLOWSTATE_RUN_ID"
        "/tasks/$FLOWSTATE_TASK_ID/artifacts/output \\\n"
        '  -H "Content-Type: application/json" \\\n'
        """  -d '{"key": "value", ...}'\n"""
        "```\n"
        "These values will be passed as input parameters to the target flow(s)."
    )


def build_routing_instructions(
    outgoing_edges: list[tuple[str, str]],
    *,
    lumon: bool = False,
) -> str:
    """Build self-report routing instructions to append to a task prompt.

    When the judge is disabled, the task agent itself decides which transition
    to take by POSTing a DECISION.json to the Flowstate API.

    When lumon=True, uses flowstate.submit_decision() instead of curl commands.

    Args:
        outgoing_edges: List of (condition, target_node_name) pairs.
        lumon: Whether to use Lumon plugin instructions instead of curl.
    """
    transitions = "\n".join(
        f'- "{condition}" → transitions to: {target}' for condition, target in outgoing_edges
    )

    if lumon:
        return (
            "\n\n## Routing Decision\n"
            "After completing your task, decide which transition to take.\n"
            "\n"
            "### Available Transitions\n"
            f"{transitions}\n"
            '\nIf no condition clearly matches, use "__none__".\n'
            "\n"
            "### Submit your decision\n"
            "```\n"
            'flowstate.submit_decision("<target_node_name>", '
            '"<brief explanation>", <0.0-1.0>)\n'
            "```\n"
            "You MUST submit this decision before completing your task."
        )

    return (
        "\n\n## Routing Decision\n"
        "After completing your task, decide which transition to take.\n"
        "\n"
        "### Available Transitions\n"
        f"{transitions}\n"
        '\nIf no condition clearly matches, use "__none__".\n'
        "\n"
        "### Submit your decision\n"
        "```bash\n"
        "curl -s -X POST $FLOWSTATE_SERVER_URL/api/runs/$FLOWSTATE_RUN_ID"
        "/tasks/$FLOWSTATE_TASK_ID/artifacts/decision \\\n"
        '  -H "Content-Type: application/json" \\\n'
        """  -d '{"decision": "<target_node_name>", """
        """"reasoning": "<brief explanation>", """
        """"confidence": <0.0-1.0>}'\n"""
        "```\n"
        "You MUST submit this decision before completing your task."
    )


def lumon_plugin_dir() -> str:
    """Return the absolute path to the bundled flowstate Lumon plugin directory.

    This directory contains manifest.lumon, impl.lumon, and flowstate_plugin.py.
    It should be symlinked or copied into the Lumon plugins directory when
    setting up a Lumon-enabled task.
    """
    return os.path.join(os.path.dirname(__file__), "lumon_plugin")
