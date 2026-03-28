# [ENGINE-065] Redesign sandbox to use a named persistent sandbox

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: ENGINE-064
- Blocks: —

## Spec References
- specs.md Section 3.3 — "Flow Declaration" (sandbox behavior)

## Summary
Replace the per-task sandbox lifecycle with a named persistent sandbox model. The user creates a sandbox once (e.g., `flowstate-claude`), runs `claude login` inside it, and configures the sandbox name in `flowstate.toml`. Flowstate reuses this sandbox for all tasks by running agent commands inside it via `openshell sandbox create` with a matching image. This eliminates the credential problem (login persists) and the provisioning delay (image is cached).

## Acceptance Criteria
- [ ] `FlowstateConfig` has a `sandbox_name: str = "flowstate-claude"` field
- [ ] Config loaded from `[sandbox] name = "..."` in flowstate.toml
- [ ] `SandboxManager.wrap_command()` uses the configured sandbox name to run commands
- [ ] Pre-flight check verifies the named sandbox exists and is Ready via `openshell sandbox get <name>`
- [ ] Helpful error message when sandbox doesn't exist (with setup instructions)
- [ ] No per-task sandbox creation/deletion — the sandbox is managed by the user
- [ ] Remove Dockerfile, credential upload, `--no-keep` complexity
- [ ] E2E: a sandboxed flow runs successfully inside the persistent sandbox

## Technical Design

### Files to Create/Modify
- `src/flowstate/config.py` — add sandbox_name field
- `src/flowstate/engine/sandbox.py` — simplify to use named sandbox
- `src/flowstate/engine/executor.py` — remove register/destroy calls for sandbox
- `src/flowstate/server/routes.py` — update pre-flight check
- `src/flowstate/server/websocket.py` — update pre-flight check
- `tests/engine/test_sandbox.py` — update tests
- `tests/engine/test_executor.py` — update tests
- `tests/server/test_sandbox_preflight.py` — update tests
- Remove `src/flowstate/engine/sandbox/Dockerfile` and `src/flowstate/engine/sandbox/` directory

### Key Implementation Details

**`config.py`:**
Add to FlowstateConfig:
```python
sandbox_name: str = "flowstate-claude"
```

Parse from TOML:
```toml
[sandbox]
name = "flowstate-claude"
```

**`sandbox.py` — simplified:**
```python
@dataclass
class SandboxManager:
    sandbox_name: str = "flowstate-claude"

    def wrap_command(self, command: list[str]) -> list[str]:
        """Wrap a command to run inside the named persistent sandbox."""
        agent_cmd = " ".join(shlex.quote(c) for c in command)
        return [
            "openshell", "sandbox", "create",
            "--name", f"{self.sandbox_name}-task",
            "--from", self.sandbox_name,  # Use the named sandbox as image source
            "--auto-providers",
            "--no-tty",
            "--no-keep",
            "--", "bash", "-c", f"exec {agent_cmd}",
        ]
```

Actually, a better approach: since `connect` doesn't support `-- command`, and `create` provisions a new sandbox each time, the simplest model is:
- The user's named sandbox has Claude Code installed + logged in
- For each task, we DON'T create a new sandbox — we just need to run the command inside the existing one
- We can use `openshell sandbox connect` with stdin piping (the approach we validated works)

The `wrap_command()` would return a script that:
1. Pipes `exec claude-agent-acp` into `openshell sandbox connect <name>`
2. Then forwards all subsequent stdin/stdout

**`executor.py`:**
- Remove `register()` and `destroy()` calls in the sandbox block
- Just call `wrap_command()` and create AcpHarness with longer timeout

**Pre-flight check (`routes.py`):**
```python
# Verify named sandbox exists and is Ready
proc = await asyncio.create_subprocess_exec(
    "openshell", "sandbox", "get", config.sandbox_name,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
stdout, stderr = await proc.communicate()
if proc.returncode != 0 or b"Ready" not in stdout:
    raise FlowstateError(
        f"Sandbox '{config.sandbox_name}' not found or not ready. "
        f"Create it: openshell sandbox create --name {config.sandbox_name} --from <dockerfile> --auto-providers\n"
        f"Then login: openshell sandbox connect {config.sandbox_name} && claude login",
        status_code=400,
    )
```

### Edge Cases
- Sandbox name not configured — use default "flowstate-claude"
- Named sandbox exists but not Ready (Provisioning/Error) — clear error
- Named sandbox doesn't have Claude Code installed — agent fails with clear error
- Named sandbox doesn't have claude login done — "Internal error" from agent
- Multiple concurrent tasks in same sandbox — openshell handles this (each connect is independent)

## Testing Strategy
- Unit tests for simplified wrap_command
- Unit tests for pre-flight sandbox existence check
- E2E: configure sandbox name, start flow, verify agent runs

## E2E Verification Plan

### Verification Steps
1. Create sandbox: `openshell sandbox create --name flowstate-claude --from /tmp/flowstate-sandbox --auto-providers --no-tty`
2. Login: `openshell sandbox connect flowstate-claude` then `claude login`
3. Start server with config: `uv run flowstate serve`
4. Start sandboxed flow run
5. Verify agent executes inside sandbox

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
