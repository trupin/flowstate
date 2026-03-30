# [ENGINE-073] Add network policy for sandbox-to-host API access

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
- specs.md Section 9.6 — "API-Based Artifact Protocol"

## Summary
Sandbox agents cannot reach the host Flowstate API because OpenShell's network policy proxy blocks all outbound traffic by default (403 Forbidden). Add a default network policy YAML that allows egress to `host.docker.internal` on the Flowstate server port. Apply it automatically when creating or using a sandbox. Also ensure the Flowstate server binds to `0.0.0.0` (not `127.0.0.1`) when sandbox mode is active, since Docker routes `host.docker.internal` through the host's network stack.

## Acceptance Criteria
- [ ] A default sandbox policy YAML file exists at `src/flowstate/engine/sandbox/policy.yaml`
- [ ] The policy allows egress to `host.docker.internal` on the configured server port
- [ ] The policy is applied automatically via `openshell policy set` when starting a sandbox flow
- [ ] The Flowstate server documentation notes that `0.0.0.0` binding is required for sandbox flows
- [ ] Sandbox agents can successfully `curl http://host.docker.internal:PORT/api/flows` from inside the sandbox

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/sandbox/policy.yaml` — default network policy
- `src/flowstate/engine/sandbox.py` — add `apply_policy()` method
- `src/flowstate/server/routes.py` — call `apply_policy()` in sandbox preflight check

### Key Implementation Details

**Default policy (`policy.yaml`):**
```yaml
version: 1
network_policies:
  flowstate_api:
    endpoints:
      - host: host.docker.internal
        port: 9090
    binaries:
      - path: "**"
```

Note: the port should be configurable. The `apply_policy()` method should generate the policy YAML dynamically using the configured server port.

**Apply policy (`sandbox.py`):**
```python
async def apply_policy(self, server_port: int) -> bool:
    """Apply network policy allowing sandbox-to-host API access."""
    policy = {
        "version": 1,
        "network_policies": {
            "flowstate_api": {
                "endpoints": [{"host": "host.docker.internal", "port": server_port}],
                "binaries": [{"path": "**"}],
            }
        }
    }
    # Write to temp file, run openshell policy set
    ...
```

**Integration in routes.py:**
Call `apply_policy()` in `_check_sandbox_requirements()` after verifying the sandbox exists.

### Edge Cases
- Policy already applied (idempotent): `openshell policy set` is idempotent
- Non-default server port: use `config.server_port`
- `openshell policy set` fails: log warning, continue (sandbox may work if policy was set manually)

## Testing Strategy
- Manual test: verify `curl http://host.docker.internal:9090/api/flows` works from inside sandbox after policy applied

## E2E Verification Plan

### Verification Steps
1. Start server on 0.0.0.0: `uv run flowstate server --host 0.0.0.0`
2. Apply policy: implemented automatically
3. From sandbox: `curl http://host.docker.internal:9090/api/flows`
4. Run `uv run pytest tests/e2e/test_sandbox.py -v`

## E2E Verification Log

### Post-Implementation Verification
_[Agent fills this in]_

## Completion Checklist
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in
