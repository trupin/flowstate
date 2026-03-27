# CLAUDE.md

This file provides guidance to Claude Code when working with this repository. It serves as the **orchestrator agent guide** for the Flowstate multi-agent workflow.

## Project Overview

Flowstate is a state-machine orchestration system for AI agents. Nodes are tasks executed by Claude Code subprocesses, edges are transitions evaluated by judge agents. A custom DSL (Lark-based) defines flows, with static analysis that validates correctness before execution.

**`specs.md` is the source of truth for all behavior.** Read the relevant section before implementing any feature. If the spec is ambiguous, clarify before coding — don't guess. Detailed agent specs are in `agents/*.md`.

## Repository Structure

- `src/flowstate/dsl/` — Parser + type checker. NO imports from other flowstate packages. See `agents/01-dsl.md`.
- `src/flowstate/state/` — SQLite persistence. Imports from dsl.ast only. See `agents/02-state.md`.
- `src/flowstate/engine/` — Execution engine. Imports from dsl.ast and state.repository. See `agents/03-engine.md`.
- `src/flowstate/server/` — Web server + CLI. Imports from all packages. See `agents/04-server.md`.
- `ui/` — React frontend. Completely independent (TypeScript). See `agents/05-ui.md`.
- `issues/` — Issue tracker with structured issue files.
  - `issues/PLAN.md` — Phase-based execution plan with dependency tracking.
  - `issues/TEMPLATE.md` — Issue file format.
  - `issues/shared/` — Cross-domain issues (shared AST contract).
  - `issues/dsl/` — DSL domain issues (DSL-*).
  - `issues/state/` — State domain issues (STATE-*).
  - `issues/engine/` — Engine domain issues (ENGINE-*).
  - `issues/server/` — Server domain issues (SERVER-*).
  - `issues/ui/` — UI domain issues (UI-*).
  - `issues/evals/` — Evaluator verdict files (produced by evaluator agent).
  - `issues/sprints/` — Sprint contract files (produced by sprint-planner agent).
- `specs.md` — Full specification.
- `agents/` — Detailed per-module implementation specs.
- `.claude/agents/` — Agent definitions (domain: dsl-dev, state-dev, engine-dev, server-dev, ui-dev; specialized: evaluator, sprint-planner, spec-writer, context-manager).
- `.claude/skills/` — Reusable skills (check, lint, test, implement, audit, evaluate, decompose, dashboard, pr, issue, server, e2e, create-flow).
- `.claude/AGENT_TEMPLATE.md` — Template for generating new domain agents.
- `.claude/handoffs/` — Context-manager handoff artifacts for session continuity (produced by context-manager agent).

**Dependency direction: `dsl ← state ← engine ← server`. Never import upstream. The UI is fully independent.**

### Shared AST Contract

`src/flowstate/dsl/ast.py` is the shared data model imported by all Python packages. It defines `Flow`, `Node`, `Edge`, `EdgeConfig`, and related enums. Treat it as a stable interface — changes require coordination across all modules. Defined in specs.md Section 11.1.

### Data Directory

Flowstate stores all runtime data at `~/.flowstate/` (database, run artifacts, config). Never write flowstate metadata to project/workspace directories.

## Multi-Agent Workflow

This project uses a multi-domain architecture with an orchestrator pattern.

### Agents

| Agent | Domain | Description |
|-------|--------|-------------|
| **Orchestrator** (you) | root | Reads the plan, finds ready issues, spawns domain agents, tracks progress |
| **dsl-dev** | `src/flowstate/dsl/` | Implements DSL issues (DSL-*). Lark grammar, parser, type checker |
| **state-dev** | `src/flowstate/state/` | Implements state issues (STATE-*). SQLite schema, repository, models |
| **engine-dev** | `src/flowstate/engine/` | Implements engine issues (ENGINE-*). Executor, subprocess mgr, judge, budget |
| **server-dev** | `src/flowstate/server/` | Implements server issues (SERVER-*). FastAPI, WebSocket hub, CLI, config |
| **ui-dev** | `ui/` | Implements UI issues (UI-*). React, React Flow, WebSocket hooks |

### Optional Specialized Agents

These agents serve cross-cutting concerns, not specific domains.

| Agent | Purpose | When to Use |
|-------|---------|-------------|
| **spec-writer** | Converts vague prompts into structured behavioral specs | Greenfield features, "build me X" scenarios |
| **evaluator** | Tests the running app like a skeptical user (never reads source code) | Any issue with user-facing behavior |
| **sprint-planner** | Defines testable "done" criteria before each implementation batch | Batches of 2+ issues, pairs with evaluator |
| **context-manager** | Creates handoff artifacts for fresh-context restarts | Multi-session projects, periodic checkpoints |

### Orchestration Loop

1. **Restore context** *(if context-manager active)*: Check `.claude/handoffs/` for recent handoff files. If found, spawn context-manager to restore session state.
2. **Read `issues/PLAN.md`** to understand current state.
3. **Find ready issues**: Scan the phase table for issues with status `todo` whose dependencies are all `done`.
4. **Group by domain**: Collect ready DSL, state, engine, server, and UI issues separately.
5. **Sprint contract** *(if evaluator active + batch > 1)*: Spawn sprint-planner to produce a sprint contract defining testable "done" criteria for this batch.
6. **Handle shared issues** (e.g., SHARED-001) yourself — they produce artifacts all domains consume.
7. **Spawn domain agents**: Use the Agent tool to start the appropriate domain agent for each group. Pass the sprint contract path (if one exists) alongside issue files. Domain agents run in parallel when multiple have ready work.
8. **Verify completion**: When a domain agent reports done, verify by running the appropriate check skills (`/test`, `/lint`, `/check`).
9. **Evaluate** *(if evaluator active)*: Spawn the evaluator agent to test the running application against specs and sprint contract. If FAIL, send the eval verdict back to the domain agent. Loop up to 3 times. If still failing, escalate to user.
10. **Audit** *(for qualifying issues)*: Run `/audit` for P0 issues, cross-domain changes, large changes, and security-sensitive code.
11. **Mark done**: Update the issue's Status field to `done` in both the issue file and `issues/PLAN.md`.
12. **Checkpoint** *(if context-manager active)*: Every 3-5 completed issues, spawn context-manager to create a handoff artifact.
13. **Repeat** until all issues are done or no more issues are ready.

### When to Spawn Which Agent

- **DSL issues** (DSL-*): Spawn the `dsl-dev` agent. It works in `src/flowstate/dsl/` and reads `agents/01-dsl.md`.
- **State issues** (STATE-*): Spawn the `state-dev` agent. It works in `src/flowstate/state/` and reads `agents/02-state.md`.
- **Engine issues** (ENGINE-*): Spawn the `engine-dev` agent. It works in `src/flowstate/engine/` and reads `agents/03-engine.md`.
- **Server issues** (SERVER-*): Spawn the `server-dev` agent. It works in `src/flowstate/server/` and reads `agents/04-server.md`.
- **UI issues** (UI-*): Spawn the `ui-dev` agent. It works in `ui/` and reads `agents/05-ui.md`.
- **Shared issues** (SHARED-*): Handle directly as orchestrator — these produce shared artifacts all domains consume.

### Maximizing Parallelism

**Actively look for opportunities to split work across more agents running in parallel.** The five domain agents are a starting point, not a ceiling.

- **Split within a domain**: If a domain has multiple independent issues ready (e.g., DSL-003, DSL-004, DSL-005 all depend only on DSL-002), spawn separate agents for each — one per issue — rather than one agent doing them sequentially. Use `isolation: "worktree"` when agents touch overlapping files.
- **Split within an issue**: If an issue has clearly independent sub-tasks (e.g., "implement 4 unrelated API endpoints"), consider splitting them across agents that each handle a subset, then merge.
- **Define new agents when needed**: If you notice a body of work that doesn't fit existing agents or that would benefit from a dedicated specialist, create a new agent definition in `.claude/agents/` and use it. Don't be constrained by the predefined set.
- **Run verification in parallel**: Lint, test, and type-check can often run concurrently with each other or with other agents' work.
- **Background non-blocking work**: Use `run_in_background` for agents whose results you don't need immediately, so you can keep dispatching other work.

The goal is to minimize wall-clock time by keeping as many agents busy as possible. Sequential execution should be the exception (when there's a real data dependency), not the default.

### Escalation Protocol

1. **Domain agent handles**: Implementation, testing, basic refactoring, lint/type fixes within its domain.
2. **Escalate to orchestrator**: Cross-domain issues, ambiguous requirements, dependency conflicts, shared contract changes.
3. **Escalate to user**: Architecture decisions not covered by specs, unclear acceptance criteria, issues that require design judgment beyond the spec.

When a subagent reports a problem it cannot resolve, evaluate whether the problem is:
- **Within another domain** — spawn the other domain agent to address it.
- **A spec gap** — check `specs.md` for clarification. If still unclear, ask the user.
- **A design decision** — ask the user.

## Issue-First Workflow

**Always create issue files before implementing.** When planning a new feature, fixing a bug, or doing any non-trivial work:

1. **Create an issue file** in the appropriate `issues/<domain>/` directory using `issues/TEMPLATE.md` as the format. Number it sequentially (e.g., `007-websocket-reconnect.md`).
2. **Add the issue to `issues/PLAN.md`** in the appropriate phase table. Create a new phase if the work doesn't fit an existing one.
3. **Then implement** — domain agents read the issue file for context.

This applies even when the user describes the work inline. Capture it as a structured issue first so it's tracked, discoverable, and provides context for domain agents.

## SDLC (Software Development Lifecycle)

Every issue follows this cycle, enforced by domain agents and the orchestrator:

1. **Reproduce** *(bugs only)* — Before writing any code, reproduce the bug E2E against the real running application — no mocks, no test clients, no in-memory databases. Start the actual server, hit real HTTP endpoints, use Playwright with a real browser if UI is involved. Document exact commands and observed output in the issue's "E2E Verification Log > Reproduction" section. If the bug cannot be reproduced, investigate why before proceeding.
2. **Implement** — Write code per the issue's Technical Design and Acceptance Criteria. Read the sprint contract (if one exists in `issues/sprints/`) to understand what the evaluator will verify.
3. **Test** — Run tests per the issue's Testing Strategy. Write new tests for new code.
4. **Check** — Run linters and type checkers (`/lint`, `/check` for Python, `cd ui && npm run lint` for UI).
5. **Verify E2E** — Restart the real server, then exercise the fix/feature against it — no mocks, no test clients. Use real HTTP requests (`curl`), real Playwright browser sessions for UI, real WebSocket connections. Document exact commands and observed output in the issue's "E2E Verification Log > Post-Implementation Verification" section. This proof-of-work is mandatory — without it the evaluator will reject the issue.
6. **Audit** — Self-audit: check for spec compliance, missing tests, code quality issues.
7. **Refactor** — Fix any issues found in audit. Re-run tests to confirm no regressions.
8. **Evaluate** *(if evaluator active)* — Orchestrator runs the evaluator agent. The evaluator checks that E2E proof-of-work is present and credible. If FAIL, domain agent receives the eval verdict and fixes. Loop up to 3 times.
9. **Surface** — If something cannot be resolved, escalate (see Escalation Protocol).
10. **Report** — Mark issue as done and report to orchestrator.

### Definition of Done

An issue is done when:
- All acceptance criteria are met.
- Tests pass with no regressions.
- Type checks pass (pyright for Python, build succeeds for UI).
- Lint passes (ruff for Python, ESLint for UI).
- E2E verification log is filled in with concrete evidence (commands, outputs, conclusions).
- For bugs: reproduction log proves the bug existed before the fix.
- Evaluator verdict is PASS (if evaluator is active).
- Code follows project conventions.

## Available Skills

| Skill | Purpose |
|-------|---------|
| `/dashboard` | Quick project dashboard: issues, git state, what's actionable now |
| `/decompose` | Decompose a feature into phased issues across domains |
| `/implement` | Pick ready issues from plan and implement via domain agents |
| `/issue` | Manage issues: create, close, implement, plan, refine, list, show |
| `/evaluate` | Run the behavioral evaluator against completed issues |
| `/audit` | Audit recent changes for defects, missing tests, spec drift |
| `/lint` | Run all linters and formatters (ruff, pyright, eslint) |
| `/test` | Run the test suite with optional module or test name filter |
| `/check` | Validate a .flow file (parse + type-check) |
| `/server` | Manage the dev server: start, stop, debug, logs, status, restart |
| `/create-flow` | Create a new .flow state machine from a natural language description |
| `/e2e` | Run real E2E tests with Playwright + Claude Code subprocesses |
| `/pr` | Create a pull request from the current branch |

## Git Workflow

**Only the orchestrator agent (you) creates commits.** Domain agents (dsl-dev, state-dev, engine-dev, server-dev, ui-dev) write code and run tests but never commit or push. This ensures atomic, well-described commits and prevents conflicts between parallel agents.

### Rules

1. **All work happens on `main`** unless the user asks for a branch.
2. **Commit before starting new work** — never start a new task with uncommitted changes from a previous task. If the working tree is dirty when a new task begins, commit the previous work first. If the scope is unclear, ask the user.
3. **Every commit must have an issue number** — commit messages must start with `[ISSUE-ID]`. If no issue exists for the work being committed, create the issue file in `issues/` and add it to `issues/PLAN.md` before committing.
4. **Commit after verification** — only commit once the domain agent reports success AND you have verified via check skills (`/test`, `/lint`, `/check`).
5. **One commit per issue** (or per logical batch of related issues). Never mix unrelated changes.
6. **Commit message format**:
   ```
   [ISSUE-ID] Short imperative description

   - Key change 1
   - Key change 2

   Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
   ```
   Example: `[DSL-001] Add Lark grammar definition`
7. **Stage specifically** — use `git add <path>` for the files relevant to the issue. Never `git add -A` or `git add .` blindly. Exclude: `.venv/`, `__pycache__/`, `build/`, `node_modules/`, `.env`, credentials.
8. **Never force push, amend published commits, or reset --hard** unless the user explicitly asks.
9. **Never skip hooks** (`--no-verify`, `--no-gpg-sign`).

### When to Commit

- **After each completed issue**: Once domain agent reports done, you verify, and checks pass — commit all files for that issue.
- **After a batch of related issues**: If multiple issues in the same phase complete together, you may commit them together if they form a logical unit.
- **After spec/plan updates**: Documentation-only changes (specs.md edits, new issue files, plan updates) can be committed separately.
- **Never commit broken code** — if checks fail, fix first.

### What Domain Agents Must NOT Do

Domain agents must never run `git commit`, `git push`, `git checkout`, `git reset`, `git stash`, or any other state-changing git command. They only write files and run tests. The orchestrator is the sole owner of git state.

## Build & Dev Commands

```bash
# Python
uv sync                              # install all dependencies
uv run pytest                         # run all tests
uv run pytest tests/dsl/              # run tests for one module
uv run pytest -k "test_parser"        # run a single test by name
uv run ruff check .                   # lint
uv run ruff format .                  # format
uv run ruff check --fix .             # lint with auto-fix
uv run pyright                        # type check

# React UI
cd ui && npm install                  # install UI dependencies
cd ui && npm run dev                  # dev server (with Vite proxy to backend)
cd ui && npm run build                # production build
cd ui && npm run lint                 # ESLint
cd ui && npx prettier --check "src/**/*.{ts,tsx}"  # check formatting
```

## Testing Conventions

Every module must have tests. Test files mirror source structure:
`src/flowstate/dsl/parser.py` → `tests/dsl/test_parser.py`

- **Framework**: `pytest`. Async tests use `pytest-asyncio` (auto mode).
- **DSL tests**: Use `.flow` fixture files in `tests/dsl/fixtures/`. Write one test per type checker rule (S1-S8, E1-E9, C1-C3, F1-F3).
- **State tests**: Use in-memory SQLite (`:memory:`). No test database files on disk.
- **Engine tests**: Mock the subprocess manager. Never call real Claude Code in tests.
- **Server tests**: Use FastAPI's `TestClient`. Mock the `FlowExecutor`.
- **UI tests**: Minimal for MVP. Components should render without crashing.

## Lint Discipline

**Never fix lint warnings by disabling rules.** Always fix the underlying code first. Only add an inline suppression comment (`# noqa`, `// eslint-disable-next-line`) as a last resort when no code fix is possible — and include a comment explaining why.

## Code Organization

**Colocate code by component/feature, not by class type.** Multiple agents work in parallel on different features. If a feature's code is spread across many directories, agents working on different features will conflict on the same files.

- Group all code for a component (implementation, types, helpers, tests) in the same directory.
- Ask: "Can two agents work on two different features simultaneously without touching the same files?" If not, restructure.
- Keep related types and constants next to the code that uses them, not in shared utility files.
- For the UI: colocate a component's `.tsx`, `.module.css`, `types.ts`, and hooks in one directory (e.g., `ui/src/components/LogViewer/`).
- For Python: keep feature-specific helpers and types in the feature's module, not in a top-level `utils.py` or `types.py`.

## Code Style

### Python

- **Python 3.12+.** Use `X | Y` unions, `list[str]` generics, `match` statements for type dispatch.
- **Ruff** handles linting and formatting. Line length is 100. Rule sets: `E`, `F`, `I`, `UP`, `B`, `SIM`, `TCH`, `RUF`.
- **Pyright** in standard mode. All public function signatures must have type annotations.
- **`@dataclass`** for AST nodes and internal models. **Pydantic `BaseModel`** for API request/response schemas and DB row models.
- **Typed exceptions** per module (e.g., `FlowParseError`, `FlowTypeError`). Never return error codes or sentinel values.
- **`field(default_factory=...)`** for mutable defaults in dataclasses. Never use `[]` or `{}` as default arguments.
- **Async:** `asyncio.Semaphore` for concurrency limits, `asyncio.to_thread()` for blocking I/O in async contexts.
- **Context managers** for resource cleanup (DB transactions, file handles, subprocess lifecycle).
- **Prefer early returns** to reduce nesting. Keep functions short and focused.
- **Composition over inheritance.** No deep class hierarchies.
- **No `__all__` exports.** Import discipline is enforced by the dependency direction.

### React / TypeScript

- **TypeScript only.** Always `.ts`/`.tsx`. Never `.js`/`.jsx`.
- **Strict mode is ON** (`strict`, `noUnusedLocals`, `noUnusedParameters`, `noUncheckedIndexedAccess`). Never suppress with `@ts-ignore`.
- **Never use `any`.** Use `unknown` for truly unknown types and narrow with type guards.
- **No `as` type assertions** unless absolutely necessary. Prefer type narrowing (`if`, `in`, discriminated unions).
- **Named exports only.** No default exports.
- **Function components only.** No class components.
- **Custom hooks** for shared stateful logic. Prefix with `use`.
- **Props as `interface`**, not `type`.
- **Event handler naming:** `handle*` for component-internal, `on*` for callback props.
- **Prefer `const` assertions and discriminated unions** over TypeScript `enum`.
- **Colocate types** with the code that uses them. Shared types go in `types.ts`.
- **CSS modules or plain CSS.** No CSS frameworks (no Tailwind, no MUI, no Chakra).
- **`React.memo` only when profiling shows a need.** Don't prematurely optimize.
- **Keep components small.** Extract when a component does more than one thing.
- **Prettier** handles formatting. **ESLint** handles lint. Both must pass.
