# [ENGINE-072] Replace connect-wrapper with ssh -T for clean ACP stdio

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: E2E-015

## Spec References
- specs.md Section 9.7 — "Worktree Isolation" (sandbox)

## Summary
Replace the `connect-wrapper.sh` script (which uses `openshell sandbox connect` with TTY allocation) with an SSH-based wrapper using `ssh -T` (no TTY). The current approach injects terminal escape codes (`\x1b[?2004h`, bracketed paste mode, command echo, `\r`) into the JSON-RPC stdio stream, corrupting ACP communication. Using `ssh -T` with `openshell ssh-proxy` as the ProxyCommand provides a clean binary stdio channel with no terminal contamination.

## Acceptance Criteria
- [ ] `connect-wrapper.sh` replaced with `ssh -T` based approach
- [ ] No `sleep` race condition
- [ ] No `stty` needed
- [ ] ACP JSON-RPC communication works reliably through the wrapper
- [ ] The single Landlock warning line at connection start is auto-skipped by ACP
- [ ] `SandboxManager.wrap_command()` updated to use the new wrapper
- [ ] Existing sandbox tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/sandbox/connect-wrapper.sh` — rewrite to use `ssh -T`
- `src/flowstate/engine/sandbox.py` — update `wrap_command()` if needed

### Key Implementation Details

Replace connect-wrapper.sh:
```bash
#!/usr/bin/env bash
set -euo pipefail

SANDBOX_NAME="$1"
AGENT_CMD="$2"

exec ssh -T \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o GlobalKnownHostsFile=/dev/null \
  -o LogLevel=ERROR \
  -o "ProxyCommand=openshell ssh-proxy --gateway-name openshell --name ${SANDBOX_NAME}" \
  "sandbox@openshell-${SANDBOX_NAME}" \
  "exec ${AGENT_CMD}"
```

### Edge Cases
- `openshell ssh-proxy` path may vary — use bare command name (should be on PATH if openshell is installed)
- Landlock warning line at connection start — ACP library already auto-skips non-JSON lines
- SSH connection timeout — rely on ACP init_timeout (120s for sandbox)

## Testing Strategy
- Manual test: run a sandbox flow and verify ACP communication is clean
- Verify the single Landlock warning is logged and skipped

## E2E Verification Plan

### Verification Steps
1. `uv run pytest tests/e2e/test_sandbox.py::TestSandboxLinear -v`
2. Verify no JSON-RPC parse errors in logs (except the expected Landlock warning)

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in
