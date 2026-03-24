# [SHARED-005] ACP harness abstraction — support any agent via Agent Client Protocol

## Domain
shared

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: —
- Blocks: —
- Sub-issues: DSL-011, ENGINE-033, ENGINE-034, SERVER-013

## Spec References
- specs.md Section 9 — "Claude Code Integration"
- specs.md Section 3.2 — "Flow Declaration"
- specs.md Section 3.4 — "Node Declarations"

## Summary
Replace the hardcoded Claude Code subprocess manager with an abstraction based on ACP (Agent Client Protocol — https://agentclientprotocol.com). ACP is a JSON-RPC 2.0 protocol over stdio that standardizes client↔agent communication. By making Flowstate an ACP client, any ACP-compatible agent harness (Claude Code, Gemini CLI, custom agents, LangGraph, CrewAI, etc.) can serve as the execution backend. The DSL gains a `harness` attribute at flow level (default for all nodes) and per-node (override), allowing heterogeneous flows where different nodes use different agent runtimes.

## Acceptance Criteria
- [ ] ACP client implementation: Flowstate can connect to any ACP-compatible agent via stdio subprocess
- [ ] DSL `harness` attribute at flow level: `harness = "claude"` (default), `harness = "gemini"`, etc.
- [ ] DSL `harness` attribute per-node: overrides flow-level harness for that node
- [ ] Harness registry: maps harness names to executable commands and ACP client config
- [ ] Existing `SubprocessManager` behavior preserved when `harness = "claude"` (backward compat)
- [ ] StreamEvent abstraction maps ACP `session/update` notifications to Flowstate's StreamEvent types
- [ ] Judge/self-report routing works with any harness (DECISION.json is agent-agnostic)
- [ ] Session resume works for harnesses that support it (ACP `session/load`)
- [ ] `kill()` sends ACP `session/cancel` to gracefully stop agents
- [ ] Config: harness definitions in `flowstate.toml` with command, args, env
- [ ] All existing tests pass with the default `claude` harness

## Technical Design

### Architecture

```
                    ┌─────────────────────────┐
                    │     FlowExecutor        │
                    │  (unchanged interface)   │
                    └──────────┬──────────────┘
                               │
                    ┌──────────▼──────────────┐
                    │   HarnessManager        │
                    │  resolve(harness_name)   │
                    │  → AcpHarness instance   │
                    └──────────┬──────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼──────┐ ┌──────▼───────┐ ┌──────▼───────┐
     │ ClaudeHarness │ │ GeminiHarness│ │ CustomHarness│
     │ (ACP client)  │ │ (ACP client) │ │ (ACP client) │
     └───────────────┘ └──────────────┘ └──────────────┘
              │                │                │
         claude CLI      gemini CLI       custom binary
         (ACP agent)     (ACP agent)      (ACP agent)
```

### ACP Protocol Integration

ACP uses JSON-RPC 2.0 over stdio. The lifecycle maps to Flowstate's execution model:

| Flowstate Operation | ACP Method | Notes |
|---|---|---|
| Start task | `initialize` → `session/new` → `session/prompt` | Fresh session per task |
| Resume task | `session/load` → `session/prompt` | For `context = session` mode |
| Stream events | `session/update` notifications | Map to StreamEvent types |
| Task complete | `session/prompt` response with `stopReason` | `end_turn` = success |
| Cancel task | `session/cancel` | Replaces SIGTERM |
| Judge evaluation | `session/new` → `session/prompt` (with system prompt) | Judge is just another prompt |

### ACP ↔ StreamEvent Mapping

| ACP `session/update` type | Flowstate StreamEventType |
|---|---|
| `agent_message_chunk` (text) | ASSISTANT |
| `agent_message_chunk` (thinking) | ASSISTANT (with thinking content) |
| `tool_call` | TOOL_USE |
| `tool_call_update` (completed) | TOOL_RESULT |
| `plan` | SYSTEM (activity) |
| Response `stopReason: end_turn` | RESULT + SYSTEM (process_exit) |
| Response `stopReason: cancelled` | SYSTEM (process_exit, exit_code=-1) |

### Files to Create/Modify

**New files:**

`src/flowstate/engine/harness.py` — Core abstraction:
```python
class Harness(Protocol):
    """ACP-based agent harness."""
    async def run_task(self, prompt, workspace, session_id, *, skip_permissions) -> AsyncGenerator[StreamEvent, None]: ...
    async def run_task_resume(self, prompt, workspace, resume_session_id, *, skip_permissions) -> AsyncGenerator[StreamEvent, None]: ...
    async def run_judge(self, prompt, workspace, *, skip_permissions) -> JudgeResult: ...
    async def kill(self, session_id) -> None: ...

class AcpHarness(Harness):
    """Generic ACP client harness — works with any ACP-compatible agent."""
    def __init__(self, command: list[str], env: dict[str, str] | None = None): ...

class HarnessManager:
    """Resolves harness names to Harness instances."""
    def __init__(self, config: dict[str, HarnessConfig]): ...
    def get(self, name: str) -> Harness: ...
```

`src/flowstate/engine/acp_client.py` — Low-level ACP JSON-RPC client:
- Spawns subprocess, reads/writes JSON-RPC over stdio
- Implements `initialize`, `session/new`, `session/load`, `session/prompt`, `session/cancel`
- Parses `session/update` notifications into StreamEvents
- Handles `session/request_permission` by auto-approving (or using skip_permissions)

**Modified files:**

`src/flowstate/dsl/grammar.lark` — Add harness attributes:
```lark
flow_attr: ... | "harness" "=" STRING -> flow_harness
node_attr: ... | "harness" "=" STRING -> node_harness
```

`src/flowstate/dsl/ast.py` — Add fields:
```python
@dataclass(frozen=True)
class Node:
    ...
    harness: str | None = None  # per-node override

@dataclass(frozen=True)
class Flow:
    ...
    harness: str = "claude"  # default harness
```

`src/flowstate/engine/executor.py`:
- Replace `self._subprocess_mgr` with `self._harness_mgr: HarnessManager`
- Resolve harness per-node: `node.harness or flow.harness`
- Call `harness.run_task()` instead of `subprocess_mgr.run_task()`

`src/flowstate/engine/judge.py`:
- Accept `Harness` instead of `SubprocessManager`
- Use `harness.run_judge()` for judge evaluation

`src/flowstate/config.py` — Add harness configuration:
```toml
[harnesses.claude]
command = ["claude"]
default = true

[harnesses.gemini]
command = ["gemini"]
env = { GEMINI_API_KEY = "..." }
```

`src/flowstate/engine/subprocess_mgr.py`:
- Refactor into a `ClaudeHarness(AcpHarness)` that preserves existing Claude Code CLI behavior
- Keep `StreamEvent`, `JudgeResult`, etc. as the shared event types
- `SubprocessManager` becomes a thin wrapper or alias for backward compat

### Edge Cases
- Harness not found in registry → clear error at flow start, not mid-execution
- ACP agent crashes mid-session → detect EOF on stdio, emit SYSTEM exit event, trigger on_error policy
- Agent doesn't support `session/load` (no resume) → fall back to fresh session with handoff context
- Permission requests from agent → auto-approve when `skip_permissions = true`, otherwise deny with explanation
- Judge with non-Claude harness → DECISION.json self-report works regardless; judge subprocess mode needs the harness to support system prompts
- `harness = "claude"` is the default → zero config change for existing flows

### Implementation Phases

This is a large cross-cutting change. Suggested sub-issues:

1. **SHARED-005a** — DSL: Add `harness` attribute to grammar, parser, AST, type checker
2. **SHARED-005b** — Engine: Create `Harness` protocol + `HarnessManager` + refactor executor
3. **SHARED-005c** — Engine: Implement `AcpHarness` (generic ACP client over stdio)
4. **SHARED-005d** — Engine: Wrap existing `SubprocessManager` as `ClaudeHarness` for backward compat
5. **SHARED-005e** — Config: Add `[harnesses.*]` section to `flowstate.toml`
6. **SHARED-005f** — Server/UI: Expose harness info in flow API response and UI detail panel

## Testing Strategy
- Unit tests: AcpHarness with a mock ACP agent (echo_agent pattern from ACP SDK examples)
- Integration tests: ClaudeHarness passes all existing subprocess_mgr tests
- DSL tests: `harness = "gemini"` parses and type-checks; per-node override works
- E2E: Flow with mixed harnesses (mock agents) executes correctly
- Backward compat: All existing tests pass with no config changes (default `claude` harness)
- `uv run pytest tests/ --ignore=tests/e2e/ -x`
