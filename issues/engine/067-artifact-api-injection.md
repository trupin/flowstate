# [ENGINE-067] Inject API coordinates into all agent environments and artifact upload instructions

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: SERVER-022
- Blocks: ENGINE-068

## Spec References
- specs.md Section 9.6 — "API-Based Artifact Protocol"
- specs.md Section 9.3 — "Task Protocol"

## Summary
Inject `FLOWSTATE_SERVER_URL`, `FLOWSTATE_RUN_ID`, and `FLOWSTATE_TASK_ID` as environment variables into every agent subprocess — both host and sandboxed. Update all prompt builders in `context.py` to instruct agents to POST their coordination artifacts (SUMMARY.md, DECISION.json, OUTPUT.json) to the Flowstate API instead of writing files to disk. For sandboxed agents, resolve the server URL to `host.docker.internal` so the sandbox container can reach the host server.

## Acceptance Criteria
- [ ] Every agent subprocess receives `FLOWSTATE_SERVER_URL`, `FLOWSTATE_RUN_ID`, `FLOWSTATE_TASK_ID` environment variables
- [ ] For host agents: `FLOWSTATE_SERVER_URL` = `http://127.0.0.1:{port}` (from `server_base_url`)
- [ ] For sandboxed agents: `FLOWSTATE_SERVER_URL` = `http://host.docker.internal:{port}`
- [ ] `_build_directory_sections()` in context.py instructs agents to POST SUMMARY.md via curl
- [ ] `build_prompt_session()` instructs agents to POST summary via curl (has its own independent reference, does NOT call `_build_directory_sections()`)
- [ ] `build_routing_instructions()` instructs agents to POST DECISION.json via curl (not write to filesystem)
- [ ] `build_cross_flow_instructions()` instructs agents to POST OUTPUT.json via curl
- [ ] All prompt builders use env var references (`$FLOWSTATE_SERVER_URL`) so the exact URL is resolved at runtime
- [ ] Sandbox connectivity verified: agent inside sandbox can reach host API

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/context.py` — update prompt builders to use API upload instructions
- `src/flowstate/engine/executor.py` — inject env vars into harness subprocess
- `src/flowstate/engine/acp_client.py` — pass env vars through to spawned process
- `src/flowstate/engine/sdk_runner.py` — pass env vars through to spawned process
- `tests/engine/test_context.py` — update tests for new prompt content

### Key Implementation Details

**Environment variable injection (executor.py):**

In `_execute_task_v2()`, before spawning the harness, build the env dict:

```python
artifact_env = {
    "FLOWSTATE_SERVER_URL": self._resolve_server_url(use_sandbox),
    "FLOWSTATE_RUN_ID": flow_run_id,
    "FLOWSTATE_TASK_ID": task_execution_id,
}
```

Where `_resolve_server_url()` returns:
- If sandbox: replace host in `self._server_base_url` with `host.docker.internal`
- If host: use `self._server_base_url` as-is

Pass `artifact_env` to the harness alongside existing env.

**Prompt changes (context.py):**

Replace `_build_directory_sections()`:
```python
def _build_directory_sections(cwd: str, task_dir: str) -> str:
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
```

Replace `build_routing_instructions()`:
```python
def build_routing_instructions(
    outgoing_edges: list[tuple[str, str]],
) -> str:
    transitions = "\n".join(
        f'- "{condition}" → transitions to: {target}'
        for condition, target in outgoing_edges
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
```

Note: `build_routing_instructions()` no longer takes `task_dir` parameter. Update all call sites.

**ACP harness env passthrough (acp_client.py):**

The `AcpHarness.__init__` already accepts `env: dict[str, str] | None`. The executor already merges harness env. Just ensure the artifact env vars are merged into the env dict before passing to `AcpHarness`.

**Sandbox URL resolution:**

```python
def _resolve_server_url(self, use_sandbox: bool) -> str:
    base = self._server_base_url or "http://127.0.0.1:9090"
    if use_sandbox:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(base)
        return urlunparse(parsed._replace(hostname="host.docker.internal"))
    return base
```

### Edge Cases
- `server_base_url` is None (no server running, CLI mode): skip env injection, use empty string
- Sandbox on Linux: `host.docker.internal` may not resolve — log a warning. Users may need `--add-host` in openshell config
- Agent doesn't run the curl command: ENGINE-068 handles fallback/error
- Port conflicts: use whatever port the server is configured on

## Testing Strategy
- Unit tests for `_build_directory_sections()`: verify curl command includes env var references
- Unit tests for `build_routing_instructions()`: verify new format with curl POST
- Unit tests for `_resolve_server_url()`: host vs sandbox URL resolution
- Integration test: verify env vars are passed to subprocess

## E2E Verification Plan

### Verification Steps
1. Start server: `uv run flowstate server`
2. Start a flow with `sandbox = true`
3. Check agent logs: verify `$FLOWSTATE_SERVER_URL` env var is set
4. Verify agent prompt includes curl-based upload instructions
5. From inside sandbox: `curl http://host.docker.internal:9090/api/flows` should succeed

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
