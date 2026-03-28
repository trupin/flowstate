# Sprint 023

**Issues**: DSL-008, ENGINE-058
**Domains**: dsl, engine
**Date**: 2026-03-27

## Acceptance Tests

### DSL-008: Add sandbox and sandbox_policy to grammar, parser, AST, and type checker

TEST-1: Flow-level sandbox parses as true
  Given: A .flow file with `sandbox = true` in the flow declaration body
  When: The file is parsed via the flowstate parser
  Then: The resulting Flow AST has `sandbox == True`

TEST-2: Flow-level sandbox defaults to false
  Given: A .flow file with no `sandbox` attribute in the flow declaration
  When: The file is parsed via the flowstate parser
  Then: The resulting Flow AST has `sandbox == False`

TEST-3: Flow-level sandbox_policy parses a path string
  Given: A .flow file with `sandbox_policy = "policies/strict.yaml"` in the flow declaration
  When: The file is parsed via the flowstate parser
  Then: The resulting Flow AST has `sandbox_policy == "policies/strict.yaml"`

TEST-4: Flow-level sandbox_policy defaults to None
  Given: A .flow file with no `sandbox_policy` attribute in the flow declaration
  When: The file is parsed via the flowstate parser
  Then: The resulting Flow AST has `sandbox_policy is None`

TEST-5: Node-level sandbox override parses on entry node
  Given: A .flow file where an entry node declares `sandbox = true`
  When: The file is parsed via the flowstate parser
  Then: The resulting Node AST for that entry node has `sandbox == True`

TEST-6: Node-level sandbox override parses on task node
  Given: A .flow file where a task node declares `sandbox = false`
  When: The file is parsed via the flowstate parser
  Then: The resulting Node AST for that task node has `sandbox == False`

TEST-7: Node-level sandbox override parses on exit node
  Given: A .flow file where an exit node declares `sandbox = true`
  When: The file is parsed via the flowstate parser
  Then: The resulting Node AST for that exit node has `sandbox == True`

TEST-8: Node-level sandbox override parses on atomic node
  Given: A .flow file where an atomic node declares `sandbox = true`
  When: The file is parsed via the flowstate parser
  Then: The resulting Node AST for that atomic node has `sandbox == True`

TEST-9: Node-level sandbox defaults to None (inherit from flow)
  Given: A .flow file where a task node does not declare `sandbox`
  When: The file is parsed via the flowstate parser
  Then: The resulting Node AST for that task node has `sandbox is None`

TEST-10: Node-level sandbox_policy override parses
  Given: A .flow file where a task node declares `sandbox_policy = "node-policy.yaml"`
  When: The file is parsed via the flowstate parser
  Then: The resulting Node AST for that task node has `sandbox_policy == "node-policy.yaml"`

TEST-11: Node-level sandbox_policy defaults to None
  Given: A .flow file where a task node does not declare `sandbox_policy`
  When: The file is parsed via the flowstate parser
  Then: The resulting Node AST for that task node has `sandbox_policy is None`

TEST-12: SB1 error when flow sets sandbox_policy without sandbox = true
  Given: A .flow file with `sandbox_policy = "strict.yaml"` but no `sandbox = true` (sandbox defaults to false)
  When: The file is type-checked via check_flow()
  Then: A FlowTypeError with code "SB1" is returned, with a message indicating sandbox_policy requires sandbox = true

TEST-13: SB1 error when flow sets sandbox = false explicitly with sandbox_policy
  Given: A .flow file with `sandbox = false` and `sandbox_policy = "strict.yaml"`
  When: The file is type-checked via check_flow()
  Then: A FlowTypeError with code "SB1" is returned

TEST-14: SB1 error when node sets sandbox_policy with explicit sandbox = false
  Given: A .flow file where a node declares `sandbox = false` and `sandbox_policy = "node.yaml"`
  When: The file is type-checked via check_flow()
  Then: A FlowTypeError with code "SB1" is returned for that node

TEST-15: SB1 error when node sets sandbox_policy inheriting sandbox = false from flow
  Given: A .flow file with `sandbox = false` (or no sandbox attribute, defaulting to false), and a node declares `sandbox_policy = "node.yaml"` without its own `sandbox` override
  When: The file is type-checked via check_flow()
  Then: A FlowTypeError with code "SB1" is returned for that node

TEST-16: No SB1 error when node inherits sandbox = true from flow and sets sandbox_policy
  Given: A .flow file with `sandbox = true` at flow level, and a node declares `sandbox_policy = "node.yaml"` without its own `sandbox` override
  When: The file is type-checked via check_flow()
  Then: No FlowTypeError with code "SB1" is returned

TEST-17: No SB1 error when sandbox = true with sandbox_policy at flow level
  Given: A .flow file with `sandbox = true` and `sandbox_policy = "strict.yaml"`
  When: The file is type-checked via check_flow()
  Then: No FlowTypeError with code "SB1" is returned; the flow type-checks successfully

TEST-18: No SB1 error when sandbox = true with no sandbox_policy
  Given: A .flow file with `sandbox = true` and no `sandbox_policy`
  When: The file is type-checked via check_flow()
  Then: No FlowTypeError with code "SB1" is returned

TEST-19: Combined flow and node sandbox attributes parse together
  Given: A .flow file with `sandbox = true` and `sandbox_policy = "flow.yaml"` at flow level, and a task node with `sandbox = false` (overriding)
  When: The file is parsed via the flowstate parser
  Then: The Flow AST has `sandbox == True` and `sandbox_policy == "flow.yaml"`, and the task Node has `sandbox == False` and `sandbox_policy is None`

TEST-20: Existing tests pass with no regressions
  Given: The DSL-008 changes are applied to grammar, parser, AST, and type checker
  When: Running the full pytest suite
  Then: All tests pass, including all existing DSL, state, engine, and server tests

TEST-21: Lint and type checks pass
  Given: All DSL-008 code changes are applied
  When: Running ruff check and pyright
  Then: Both complete with no errors

### ENGINE-058: Implement SandboxManager for OpenShell lifecycle

TEST-22: sandbox_name generates deterministic name from task execution ID
  Given: A SandboxManager instance and a task execution ID string
  When: Calling sandbox_name() with the same ID multiple times
  Then: The same name is returned each time, and the name starts with "fs-" and contains the first 12 characters of the ID

TEST-23: sandbox_name uses first 12 characters of ID
  Given: A SandboxManager instance and a task execution ID "abcdef123456789xyz"
  When: Calling sandbox_name("abcdef123456789xyz")
  Then: The returned name is "fs-abcdef123456"

TEST-24: wrap_command transforms a basic command
  Given: A SandboxManager instance
  When: Calling wrap_command(["claude"], "abc123def456") with no sandbox_policy
  Then: The result is ["openshell", "sandbox", "create", "--name", "fs-abc123def456", "--", "claude"]

TEST-25: wrap_command includes --policy when sandbox_policy provided
  Given: A SandboxManager instance
  When: Calling wrap_command(["claude"], "abc123def456", sandbox_policy="strict.yaml")
  Then: The result is ["openshell", "sandbox", "create", "--name", "fs-abc123def456", "--policy", "strict.yaml", "--", "claude"]

TEST-26: wrap_command preserves multi-argument commands
  Given: A SandboxManager instance
  When: Calling wrap_command(["claude", "--model", "opus", "--verbose"], "abc123def456")
  Then: The result ends with ["--", "claude", "--model", "opus", "--verbose"]

TEST-27: register tracks a sandbox in the active set
  Given: A SandboxManager instance with no active sandboxes
  When: Calling register("task-exec-001") followed by checking internal state
  Then: The sandbox name for "task-exec-001" is present in the active sandboxes set

TEST-28: destroy removes sandbox from active set
  Given: A SandboxManager instance with a registered sandbox for "task-exec-001"
  When: Calling destroy("task-exec-001")
  Then: The sandbox name for "task-exec-001" is no longer in the active sandboxes set

TEST-29: destroy invokes openshell sandbox delete subprocess
  Given: A SandboxManager instance with a registered sandbox for "task-exec-001"
  When: Calling destroy("task-exec-001") (with mocked subprocess)
  Then: asyncio.create_subprocess_exec is called with ["openshell", "sandbox", "delete", "fs-task-exec-00"]

TEST-30: destroy for unregistered sandbox is a no-op
  Given: A SandboxManager instance with no active sandboxes
  When: Calling destroy("nonexistent-id")
  Then: No exception is raised; the call completes successfully (best-effort delete subprocess may still run)

TEST-31: destroy_all clears all tracked sandboxes
  Given: A SandboxManager instance with 3 registered sandboxes
  When: Calling destroy_all()
  Then: The active sandboxes set is empty, and openshell sandbox delete was called for each of the 3 sandbox names

TEST-32: destroy_all with empty set is a no-op
  Given: A SandboxManager instance with no active sandboxes
  When: Calling destroy_all()
  Then: No exception is raised and no subprocess calls are made

TEST-33: SandboxError exception exists and is importable
  Given: The sandbox module is implemented
  When: Importing SandboxError from the sandbox module
  Then: SandboxError is a valid exception class that can be instantiated and raised

TEST-34: Concurrent register/destroy operations are safe
  Given: A SandboxManager instance
  When: Multiple concurrent register() and destroy() calls are made via asyncio.gather
  Then: No race conditions occur; the active set is consistent after all operations complete

TEST-35: All engine tests pass with no regressions
  Given: The ENGINE-058 changes are applied
  When: Running the full pytest suite
  Then: All tests pass, including all existing engine tests

TEST-36: Lint and type checks pass
  Given: All ENGINE-058 code changes are applied
  When: Running ruff check and pyright
  Then: Both complete with no errors

## Out of Scope

- Actual integration of SandboxManager into the engine executor (ENGINE-059, blocked by both DSL-008 and ENGINE-058)
- Server API changes to expose sandbox settings (SERVER-020, blocked by DSL-008)
- UI display of sandbox attributes (UI-064, blocked by DSL-008)
- OpenShell installation, Docker setup, or actual sandbox creation (ENGINE-058 only builds the manager; real openshell calls are mocked in tests)
- Sandbox policy file validation or schema enforcement (the DSL only stores the path string)
- Budget enforcement or resource limits within sandboxes
- Hot-reload of sandbox policy files

## Integration Points

- DSL-008 adds `sandbox: bool` and `sandbox_policy: str | None` fields to the shared AST (`Flow` and `Node` dataclasses in `src/flowstate/dsl/ast.py`). These fields are consumed downstream by the engine, server, and UI.
- ENGINE-058 is standalone: it creates `src/flowstate/engine/sandbox.py` with no imports from the DSL. The `SandboxManager.wrap_command()` accepts a `sandbox_policy: str | None` parameter directly, which will later be resolved from the AST by ENGINE-059.
- The two issues have no direct integration between them in this sprint. They are independent foundation pieces that ENGINE-059 will wire together.
- ENGINE-058's `SandboxManager` uses the naming convention `fs-{task_execution_id[:12]}` for sandbox names. This is an internal convention with no cross-domain contract.

## Done Criteria

This sprint is complete when:
- All acceptance tests PASS in the evaluator's verdict
- `uv run pytest` passes with no regressions
- `uv run ruff check .` and `uv run pyright` pass
- No regressions in existing DSL parsing, type checking, or engine behavior
