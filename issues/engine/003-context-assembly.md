# [ENGINE-003] Context Assembly (handoff/session/none + SUMMARY.md)

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-001
- Blocks: ENGINE-005

## Spec References
- specs.md Section 9.1 — "Task Subprocess Invocation" (prompt templates for handoff/session/none/join)
- specs.md Section 9.5 — "Task Directory Setup"
- specs.md Section 2.6.1 — "Flowstate Data Directory"
- specs.md Section 2.9 — "Context Mode"
- specs.md Section 6.6 — "Cycle Re-entry"
- agents/03-engine.md — "Context Assembly"

## Summary
Implement the context assembly module that prepares everything needed before launching a Claude Code subprocess: creating task directories under `~/.flowstate/runs/<run-id>/tasks/`, constructing prompts based on context mode (handoff, session, none), aggregating SUMMARY.md files from fork members for join nodes, expanding `{{param}}` template variables, and resolving the effective context mode from edge/flow configuration. This module is the bridge between the state layer (which tracks where things are) and the subprocess manager (which runs them).

## Acceptance Criteria
- [ ] File `src/flowstate/engine/context.py` exists and is importable
- [ ] `create_task_dir(run_data_dir, node_name, generation) -> str` creates `<run_data_dir>/tasks/<name>-<gen>/` and returns the absolute path
- [ ] If the `<run_data_dir>/` directory does not exist, it is created
- [ ] `build_prompt_handoff(node, task_dir, cwd, predecessor_summary) -> str` returns the full prompt for handoff mode per specs.md Section 9.1 template
- [ ] `build_prompt_session(node, task_dir) -> str` returns the shorter session-mode prompt
- [ ] `build_prompt_none(node, task_dir, cwd) -> str` returns prompt with no upstream context
- [ ] `build_prompt_join(node, task_dir, cwd, member_summaries) -> str` returns prompt with aggregated fork member summaries
- [ ] All prompt builders include the SUMMARY.md instruction: "When you are done, you MUST write a SUMMARY.md to {task_dir}/SUMMARY.md"
- [ ] `expand_templates(text, params) -> str` replaces all `{{param_name}}` occurrences with actual values
- [ ] `get_context_mode(edge, flow) -> ContextMode` returns edge-level override if set, otherwise flow-level default
- [ ] `read_summary(task_dir) -> str | None` reads `<task_dir>/SUMMARY.md` and returns contents, or None if not found
- [ ] `resolve_cwd(node, flow) -> str` returns node.cwd if set, else flow.workspace, else raises error
- [ ] Template expansion handles string, number, and bool parameter types (converting to string representation)
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/context.py` — context assembly implementation
- `tests/engine/test_context.py` — tests

### Key Implementation Details

#### Task Directory Lifecycle

```python
import os
from pathlib import Path


def create_task_dir(run_data_dir: str, node_name: str, generation: int) -> str:
    """Create the task directory and return its absolute path.

    Creates: <run_data_dir>/tasks/<name>-<gen>/
    Also creates <run_data_dir>/ if it doesn't exist.
    """
    run_path = Path(run_data_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    tasks_path = run_path / "tasks"
    tasks_path.mkdir(exist_ok=True)

    task_dir = tasks_path / f"{node_name}-{generation}"
    task_dir.mkdir(exist_ok=True)

    return str(task_dir)
```

#### Prompt Construction

Each context mode has a specific prompt template from specs.md Section 9.1. The templates must be followed exactly:

**Handoff mode** (fresh session with predecessor context):
```
You are executing a task in a Flowstate workflow.

## Context from previous task
{predecessor_summary}

## Your task
{expanded_prompt}

## Working directory
Your working directory is: {cwd}

## Task directory
Write your working notes and scratch files to {task_dir}/.
When you are done, you MUST write a SUMMARY.md to {task_dir}/SUMMARY.md describing:
- What you did
- What changed
- The outcome / current state
```

**Session mode** (resumed session, shorter prompt):
```
## Next task: {node_name}
{expanded_prompt}

When you are done, write a SUMMARY.md to {task_dir}/SUMMARY.md
describing what you did and the outcome.
```

**None mode** (fresh session, no upstream context):
Same structure as handoff but without the "Context from previous task" section.

**Join mode** (handoff with multiple predecessors):
```
You are executing a task in a Flowstate workflow.

## Context from parallel tasks

### {member_1_name}
{member_1_summary}

### {member_2_name}
{member_2_summary}

## Your task
{expanded_prompt}

## Working directory and task directory
[same as handoff]
```

#### Template Variable Expansion

```python
import re


def expand_templates(text: str, params: dict[str, str | float | bool]) -> str:
    """Replace {{param_name}} with actual parameter values.

    Handles string, number, and bool types by converting to string.
    Unmatched template variables are left as-is (the type checker
    should have caught missing params, but defensive coding).
    """
    def replacer(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name in params:
            return str(params[name])
        return match.group(0)  # leave unmatched as-is

    return re.sub(r"\{\{(\s*\w+\s*)\}\}", replacer, text)
```

#### Context Mode Resolution

```python
from flowstate.dsl.ast import Edge, Flow, ContextMode


def get_context_mode(edge: Edge, flow: Flow) -> ContextMode:
    """Edge-level context override takes precedence over flow-level default."""
    if edge.config.context is not None:
        return edge.config.context
    return flow.context
```

#### CWD Resolution

```python
from flowstate.dsl.ast import Node, Flow


class CwdResolutionError(Exception):
    """Raised when neither node nor flow specifies a working directory."""
    pass


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
```

#### SUMMARY.md Reading

```python
def read_summary(task_dir: str) -> str | None:
    """Read SUMMARY.md from a task directory. Returns None if not found."""
    summary_path = Path(task_dir) / "SUMMARY.md"
    if summary_path.exists():
        return summary_path.read_text()
    return None
```

### Edge Cases
- **Missing SUMMARY.md for handoff mode**: The predecessor task may not have written a SUMMARY.md. `read_summary` returns None. The prompt builder should include a note like "(No summary available from predecessor task)" instead of crashing.
- **Missing SUMMARY.md for join member**: Same as above — include a "(No summary available)" note for that member.
- **Template variable not in params**: Leave `{{unknown_var}}` as-is in the prompt. The type checker validates param references statically, so this should not happen in practice.
- **Bool parameter expansion**: `True` becomes `"True"`, `False` becomes `"False"`. This matches Python's str() behavior.
- **Empty predecessor summary**: If SUMMARY.md exists but is empty, include the empty string in the prompt (do not substitute a placeholder).
- **Cycle re-entry with handoff mode**: The predecessor is the source task (the one that triggered the cycle), not the previous generation of the re-entered task. The caller (executor) is responsible for passing the correct predecessor summary.
- **Cycle re-entry with session mode**: The session to resume is the *source* task's session (the task that triggered the cycle), not the re-entered task's previous session. Again, the caller handles this.
- **Task directory already exists**: `mkdir(exist_ok=True)` handles re-creation gracefully (e.g., retry scenarios).
- **Special characters in node names**: Node names are identifiers validated by the DSL parser (`[a-zA-Z_][a-zA-Z0-9_]*`), so no path injection risk.

## Testing Strategy

Create `tests/engine/test_context.py`:

1. **test_create_task_dir** — Call `create_task_dir` with a temp directory. Verify the path `<tmp>/tasks/<name>-1/` exists. Verify the returned path is correct.

2. **test_create_task_dir_creates_parents** — Pass a non-existent run_data_dir. Verify it creates all intermediate directories.

3. **test_create_task_dir_generation_2** — Create dir for generation 2. Verify path ends with `<name>-2/`.

4. **test_create_task_dir_idempotent** — Call twice with same args. No error on second call.

5. **test_build_prompt_handoff** — Build a handoff prompt with known predecessor summary. Verify the output contains all required sections: "Context from previous task", "Your task", "Working directory", "Task directory", SUMMARY.md instruction.

6. **test_build_prompt_handoff_no_summary** — Build handoff prompt with predecessor_summary=None. Verify a fallback message is included.

7. **test_build_prompt_session** — Build a session prompt. Verify it contains "Next task:", the node prompt, and SUMMARY.md instruction. Verify it does NOT contain "Context from previous task" or "Working directory" (shorter format).

8. **test_build_prompt_none** — Build a none-mode prompt. Verify it contains "Your task" and SUMMARY.md instruction. Verify it does NOT contain "Context from previous task".

9. **test_build_prompt_join** — Build a join prompt with two member summaries. Verify "Context from parallel tasks" section with both member names and summaries.

10. **test_build_prompt_join_missing_summary** — One member has no summary. Verify fallback text for that member.

11. **test_expand_templates_string** — Expand `{{repo}}` with `{"repo": "my-repo"}`. Verify substitution.

12. **test_expand_templates_number** — Expand `{{count}}` with `{"count": 42}`. Verify "42" in output.

13. **test_expand_templates_bool** — Expand `{{verbose}}` with `{"verbose": True}`. Verify "True" in output.

14. **test_expand_templates_multiple** — Expand text with multiple different params.

15. **test_expand_templates_unknown_var** — Text contains `{{unknown}}`. Verify it remains as `{{unknown}}`.

16. **test_expand_templates_with_spaces** — `{{ repo }}` (spaces inside braces) is expanded correctly.

17. **test_get_context_mode_edge_override** — Edge has context set. Flow has a different default. Verify edge wins.

18. **test_get_context_mode_flow_default** — Edge has context=None. Verify flow default is returned.

19. **test_resolve_cwd_node_cwd** — Node has cwd set, flow has workspace. Verify node.cwd is returned.

20. **test_resolve_cwd_flow_workspace** — Node has cwd=None, flow has workspace. Verify flow.workspace.

21. **test_resolve_cwd_neither** — Neither set. Verify `CwdResolutionError` raised.

22. **test_read_summary_exists** — Write a SUMMARY.md to a temp dir. Call `read_summary`. Verify contents.

23. **test_read_summary_missing** — Call `read_summary` on a dir without SUMMARY.md. Verify None.

Use `tmp_path` pytest fixture for all filesystem tests.
