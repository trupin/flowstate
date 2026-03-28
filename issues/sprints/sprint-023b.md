# Sprint 023b

**Issues**: ENGINE-059, SERVER-020, UI-064
**Domains**: engine, server, ui
**Date**: 2026-03-27

## Acceptance Tests

### ENGINE-059: Integrate sandbox into executor task lifecycle

TEST-1: Sandbox resolution inherits flow-level sandbox = true
  Given: A .flow file with `sandbox = true` at flow level and a task node with no sandbox override
  When: The executor runs the task
  Then: The task executes with a sandboxed harness (the command is wrapped via SandboxManager)

TEST-2: Sandbox resolution respects node-level override to false
  Given: A .flow file with `sandbox = true` at flow level and a task node with `sandbox = false`
  When: The executor runs that task
  Then: The task executes with the original harness command (no sandbox wrapping)

TEST-3: Sandbox resolution respects node-level override to true
  Given: A .flow file with `sandbox = false` (or default) at flow level and a task node with `sandbox = true`
  When: The executor runs that task
  Then: The task executes with a sandboxed harness (the command is wrapped via SandboxManager)

TEST-4: Sandbox policy resolution — node policy overrides flow policy
  Given: A .flow file with `sandbox = true`, `sandbox_policy = "flow-policy.yaml"` at flow level, and a task node with `sandbox_policy = "node-policy.yaml"`
  When: The executor runs that task
  Then: The sandbox wrapping uses "node-policy.yaml" (not "flow-policy.yaml")

TEST-5: Sandbox policy resolution — inherits flow policy when node has none
  Given: A .flow file with `sandbox = true`, `sandbox_policy = "flow-policy.yaml"` at flow level, and a task node with no sandbox_policy
  When: The executor runs that task
  Then: The sandbox wrapping uses "flow-policy.yaml"

TEST-6: Sandbox disabled — execution is unchanged
  Given: A .flow file with no sandbox attributes (defaults to false)
  When: The executor runs a task
  Then: The task executes with the original harness command (no sandbox wrapping, no SandboxManager interaction)

TEST-7: Sandbox creates a new AcpHarness instance
  Given: A .flow file with `sandbox = true`
  When: The executor runs a task
  Then: A new AcpHarness instance is created with the wrapped command (the shared/original harness is not mutated)

TEST-8: Sandbox is registered before task execution
  Given: A .flow file with `sandbox = true`
  When: The executor runs a task
  Then: SandboxManager.register() is called with the task execution ID before the task subprocess starts

TEST-9: Sandbox is destroyed after successful task completion
  Given: A .flow file with `sandbox = true` and a task that completes successfully
  When: The task finishes execution
  Then: SandboxManager.destroy() is called with the task execution ID in the finally block

TEST-10: Sandbox is destroyed after task failure
  Given: A .flow file with `sandbox = true` and a task that fails with an error
  When: The task execution raises an exception
  Then: SandboxManager.destroy() is called with the task execution ID in the finally block (cleanup still happens)

TEST-11: Cancel destroys all active sandboxes
  Given: A running flow with multiple sandboxed tasks active
  When: The flow run is cancelled via executor.cancel()
  Then: SandboxManager.destroy_all() is called, cleaning up all active sandboxes

TEST-12: Multiple concurrent sandboxed tasks get unique sandboxes
  Given: A .flow file with `sandbox = true` and multiple tasks that can run concurrently
  When: The executor runs multiple tasks in parallel
  Then: Each task gets its own sandbox (registered with its unique task execution ID)

TEST-13: AcpHarness exposes command property
  Given: An AcpHarness instance created with a specific command
  When: Accessing the `command` property
  Then: Returns a list of strings matching the command it was created with

TEST-14: AcpHarness exposes env property
  Given: An AcpHarness instance created with specific environment variables
  When: Accessing the `env` property
  Then: Returns a dict matching the env it was created with (or None if no env)

TEST-15: No regressions in existing executor behavior
  Given: All ENGINE-059 changes are applied
  When: Running the full pytest suite (`uv run pytest`)
  Then: All existing tests pass with no regressions

TEST-16: Engine lint and type checks pass
  Given: All ENGINE-059 code changes are applied
  When: Running `uv run ruff check .` and `uv run pyright`
  Then: Both complete with no errors

### SERVER-020: Validate openshell availability at run start

TEST-17: Sandboxed flow without openshell returns 400
  Given: A flow with `sandbox = true` is loaded and openshell is NOT on PATH
  When: POST /api/flows/:id/runs to start a run
  Then: Response is HTTP 400 with a JSON error message mentioning "openshell" and including install instructions

TEST-18: Sandboxed flow with openshell proceeds normally
  Given: A flow with `sandbox = true` is loaded and openshell IS on PATH
  When: POST /api/flows/:id/runs to start a run
  Then: Response is HTTP 202 with a flow_run_id (run starts normally)

TEST-19: Non-sandboxed flow skips openshell check
  Given: A flow with no sandbox attributes (sandbox = false)
  When: POST /api/flows/:id/runs to start a run
  Then: Response is HTTP 202 regardless of whether openshell is installed (no pre-flight check performed)

TEST-20: Node-level sandbox = true triggers pre-flight check
  Given: A flow with `sandbox = false` at flow level but one node has `sandbox = true`, and openshell is NOT on PATH
  When: POST /api/flows/:id/runs to start a run
  Then: Response is HTTP 400 with the openshell install error message

TEST-21: Error message includes install instructions
  Given: A sandboxed flow and openshell is NOT on PATH
  When: POST /api/flows/:id/runs to start a run
  Then: The error body contains text referencing how to install openshell (e.g., a URL or command)

TEST-22: Pre-flight check also applies to task restart/retry paths
  Given: A sandboxed flow and openshell is NOT on PATH
  When: Attempting to restart or retry a task in that flow via the API
  Then: The same 400 error is returned (the check is not limited to the initial start_run endpoint)

TEST-23: No regressions in existing server behavior
  Given: All SERVER-020 changes are applied
  When: Running the full pytest suite (`uv run pytest`)
  Then: All existing tests pass with no regressions

TEST-24: Server lint and type checks pass
  Given: All SERVER-020 code changes are applied
  When: Running `uv run ruff check .` and `uv run pyright`
  Then: Both complete with no errors

### UI-064: Show sandbox indicator in flow detail panel

TEST-25: Sandbox badge visible when flow has sandbox = true
  Given: A flow with `sandbox = true` is loaded in the UI
  When: Viewing the flow detail panel for that flow
  Then: A "Sandbox" badge/indicator is visible in the flow metadata section

TEST-26: Sandbox badge hidden when flow has sandbox = false
  Given: A flow with `sandbox = false` (or no sandbox attribute) is loaded in the UI
  When: Viewing the flow detail panel for that flow
  Then: No sandbox badge/indicator is visible

TEST-27: Sandbox badge tooltip shows basic sandbox info
  Given: A flow with `sandbox = true` and no sandbox_policy is loaded in the UI
  When: Hovering over or inspecting the sandbox badge
  Then: A tooltip is shown explaining that the flow runs in OpenShell isolation

TEST-28: Sandbox badge tooltip shows policy path when set
  Given: A flow with `sandbox = true` and `sandbox_policy = "policies/strict.yaml"` is loaded in the UI
  When: Hovering over or inspecting the sandbox badge
  Then: The tooltip includes the policy path "policies/strict.yaml"

TEST-29: Flow type includes sandbox fields
  Given: The UI TypeScript types for FlowAstJson
  When: Receiving AST JSON from the backend for a flow with sandbox attributes
  Then: The sandbox (boolean) and sandbox_policy (string | null) fields are present in the type definition and correctly deserialized

TEST-30: Graceful handling of missing sandbox fields (backward compatibility)
  Given: AST JSON from an older server version that does not include sandbox fields
  When: Viewing the flow detail panel
  Then: No sandbox badge is shown and no runtime errors occur (undefined treated as false)

TEST-31: Sandbox badge styling is consistent with existing badges
  Given: A flow with `sandbox = true` and a non-default harness
  When: Viewing the flow detail panel
  Then: The sandbox badge uses styling consistent with the harness provider indicator (similar visual weight, positioning)

TEST-32: UI lint and build pass
  Given: All UI-064 code changes are applied
  When: Running `cd ui && npm run lint` and `cd ui && npm run build`
  Then: Both complete with no errors

## Out of Scope

- Actual OpenShell installation, Docker setup, or real sandbox creation (tests mock openshell)
- Sandbox policy file validation or schema enforcement (the system only stores and passes the path string)
- Budget enforcement or resource limits within sandboxes
- Hot-reload of sandbox policy files during execution
- Sandbox status display during live run execution (e.g., "sandbox active" in task logs)
- Node-level sandbox indicators in the flow graph visualization (only the flow detail panel is addressed)
- WebSocket events for sandbox lifecycle (create/destroy notifications)
- Mobile-responsive styling for the sandbox badge

## Integration Points

- **Engine domain consumes AST fields**: ENGINE-059 reads `flow.sandbox`, `flow.sandbox_policy`, `node.sandbox`, and `node.sandbox_policy` from the shared AST (added by DSL-008 in Sprint 023) to resolve sandbox settings per task.
- **Engine domain uses SandboxManager**: ENGINE-059 wires `SandboxManager` (created by ENGINE-058 in Sprint 023) into the executor lifecycle. The manager's `wrap_command()`, `register()`, `destroy()`, and `destroy_all()` methods are called from `executor.py`.
- **Server domain reads AST fields**: SERVER-020 reads `flow.sandbox` and iterates `flow.nodes` checking `node.sandbox` from the parsed AST to determine if openshell is required. No engine imports needed.
- **UI domain consumes serialized AST**: UI-064 reads `sandbox` and `sandbox_policy` fields from the JSON-serialized AST returned by `GET /api/flows/:id`. The backend already serializes the full AST to JSON, so the new fields appear automatically once DSL-008 is deployed.
- **Shared type**: `Flow` and `Node` dataclasses in `src/flowstate/dsl/ast.py` (sandbox: bool, sandbox_policy: str | None on Flow; sandbox: bool | None, sandbox_policy: str | None on Node).
- **No direct cross-domain runtime dependencies**: ENGINE-059, SERVER-020, and UI-064 are independent consumers of the AST. They do not call each other and can be implemented in parallel.

## Done Criteria

This sprint is complete when:
- All 32 acceptance tests PASS in the evaluator's verdict
- `uv run pytest` passes with no regressions
- `uv run ruff check .` and `uv run pyright` pass with no errors
- `cd ui && npm run lint` and `cd ui && npm run build` pass with no errors
- No regressions in existing executor, server route, or UI behavior
