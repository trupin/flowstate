# Flowstate Implementation Plan

## Architecture Overview

**Flowstate** is a state-machine orchestration system for AI agents. Nodes are tasks executed by Claude Code subprocesses, edges are transitions evaluated by judge agents. A custom DSL (Lark-based) defines flows, with static analysis that validates correctness before execution.

**DSL Layer** — Lark grammar + Earley parser → AST dataclasses → type checker (18 static analysis rules). The AST (`ast.py`) is the shared contract imported by all other domains.

**State Layer** — SQLite with WAL mode. Repository class provides all CRUD operations. Pydantic models for DB rows.

**Execution Engine** — Async orchestration loop managing Claude Code subprocesses, fork-join coordination, judge invocation, budget enforcement, and context assembly.

**Web Server** — FastAPI REST API + WebSocket hub for real-time events. CLI interface via typer. File watcher for `.flow` file discovery.

**Web UI** — React + TypeScript. Dark mode dashboard with graph visualization (React Flow), raw log streaming, sidebar navigation, and flow control.

**Dependency direction: `dsl ← state ← engine ← server`. The UI is fully independent (TypeScript).**

---

## Phase Table

### Phase 0 — Project Setup


| Issue      | Title                                               | Domain | Priority | Depends On | Status |
| ---------- | --------------------------------------------------- | ------ | -------- | ---------- | ------ |
| SHARED-001 | AST Definitions (shared contract)                   | shared | P0       | —          | done   |
| SHARED-002 | Project Setup (pyproject.toml, directory structure) | shared | P0       | —          | done   |


### Phase 1 — DSL


| Issue   | Title                                 | Domain | Priority | Depends On | Status |
| ------- | ------------------------------------- | ------ | -------- | ---------- | ------ |
| DSL-001 | Lark Grammar Definition               | dsl    | P0       | SHARED-001 | done   |
| DSL-002 | Parser (source → AST)                 | dsl    | P0       | DSL-001    | done   |
| DSL-003 | Type Checker (structural rules S1-S8) | dsl    | P0       | DSL-002    | done   |
| DSL-004 | Type Checker (edge rules E1-E9)       | dsl    | P0       | DSL-002    | done   |
| DSL-005 | Type Checker (cycle rules C1-C3)      | dsl    | P0       | DSL-002    | done   |
| DSL-006 | Type Checker (fork-join rules F1-F3)  | dsl    | P0       | DSL-002    | done   |
| DSL-007 | Add judge boolean parameter            | dsl    | P1       | —          | done   |


### Phase 1 — State


| Issue     | Title                                | Domain | Priority | Depends On | Status |
| --------- | ------------------------------------ | ------ | -------- | ---------- | ------ |
| STATE-001 | SQLite Schema + Database Setup       | state  | P0       | SHARED-001 | done   |
| STATE-002 | Pydantic Models                      | state  | P0       | STATE-001  | done   |
| STATE-003 | Repository (flow definitions + runs) | state  | P0       | STATE-002  | done   |
| STATE-004 | Repository (task executions + edges) | state  | P0       | STATE-002  | done   |
| STATE-005 | Repository (fork groups + logs)      | state  | P0       | STATE-002  | done   |
| STATE-006 | Repository (scheduling + recovery)   | state  | P1       | STATE-002  | done   |


### Phase 2 — Execution Engine


| Issue      | Title                                                | Domain | Priority | Depends On                                               | Status |
| ---------- | ---------------------------------------------------- | ------ | -------- | -------------------------------------------------------- | ------ |
| ENGINE-001 | Subprocess Manager (Claude Code lifecycle)           | engine | P0       | SHARED-001                                               | done   |
| ENGINE-002 | Budget Guard                                         | engine | P0       | —                                                        | done   |
| ENGINE-003 | Context Assembly (handoff/session/none + SUMMARY.md) | engine | P0       | SHARED-001                                               | done   |
| ENGINE-004 | Judge Protocol                                       | engine | P0       | ENGINE-001                                               | done   |
| ENGINE-005 | Executor — Linear Flows                              | engine | P0       | ENGINE-001, ENGINE-002, ENGINE-003, STATE-003, STATE-004 | done   |
| ENGINE-006 | Executor — Fork-Join                                 | engine | P0       | ENGINE-005, STATE-005                                    | done   |
| ENGINE-007 | Executor — Conditional + Cycles                      | engine | P0       | ENGINE-004, ENGINE-005                                   | done   |
| ENGINE-008 | Executor — Pause/Resume/Cancel/Retry/Skip            | engine | P0       | ENGINE-005                                               | done   |
| ENGINE-009 | Event System                                         | engine | P0       | ENGINE-005                                               | done   |
| ENGINE-010 | Edge Delay Scheduling                                | engine | P1       | ENGINE-005, STATE-006                                    | done   |
| ENGINE-011 | Recurring Flow Scheduling                            | engine | P1       | ENGINE-005, STATE-006                                    | done   |


### Phase 3 — Web Server + CLI


| Issue      | Title                                                      | Domain | Priority | Depends On                   | Status |
| ---------- | ---------------------------------------------------------- | ------ | -------- | ---------------------------- | ------ |
| SERVER-001 | FastAPI App + Config Loading                               | server | P0       | SHARED-002                   | done   |
| SERVER-002 | REST API — Flow Discovery (file watcher)                   | server | P0       | SERVER-001, DSL-002, DSL-003 | done   |
| SERVER-003 | REST API — Run Management                                  | server | P0       | SERVER-001, ENGINE-005       | done   |
| SERVER-004 | REST API — Task Logs + Schedules                           | server | P1       | SERVER-003, ENGINE-010       | done   |
| SERVER-005 | WebSocket Hub (event broadcasting + reconnection)          | server | P0       | SERVER-001, ENGINE-009       | done   |
| SERVER-006 | WebSocket File Watcher Events                              | server | P1       | SERVER-002, SERVER-005       | done   |
| SERVER-007 | CLI (check, server, run, runs, status, schedules, trigger) | server | P1       | SERVER-001, DSL-002          | done   |
| SERVER-008 | Static File Serving (React build)                          | server | P2       | SERVER-001                   | done   |


### Phase 4 — Web UI


| Issue  | Title                                                   | Domain | Priority | Depends On                             | Status |
| ------ | ------------------------------------------------------- | ------ | -------- | -------------------------------------- | ------ |
| UI-001 | Project Scaffold (Vite + React + TypeScript)            | ui     | P0       | —                                      | done   |
| UI-002 | Dark Theme + CSS Variables                              | ui     | P0       | UI-001                                 | done   |
| UI-003 | Sidebar Component (Flows, Active Runs, Schedules)       | ui     | P0       | UI-002                                 | done   |
| UI-004 | Graph Visualization (React Flow + dagre)                | ui     | P0       | UI-002                                 | done   |
| UI-005 | Node Component (compact pills + expandable)             | ui     | P0       | UI-004                                 | done   |
| UI-006 | Log Viewer (raw streaming)                              | ui     | P0       | UI-002                                 | done   |
| UI-007 | Control Panel (pause/resume/cancel/retry/skip + budget) | ui     | P0       | UI-002                                 | done   |
| UI-008 | WebSocket Hook + Flow Run State Hook                    | ui     | P0       | UI-001                                 | done   |
| UI-009 | API Client + TypeScript Types                           | ui     | P0       | UI-001                                 | done   |
| UI-010 | Flow Library Page                                       | ui     | P0       | UI-003, UI-004, UI-009                 | done   |
| UI-011 | Run Detail Page                                         | ui     | P0       | UI-004, UI-005, UI-006, UI-007, UI-008 | done   |
| UI-012 | Start Run Modal                                         | ui     | P1       | UI-009, UI-010                         | done   |
| UI-013 | Error Banner (file watcher errors)                      | ui     | P1       | UI-008, UI-010                         | done   |
| UI-014 | Flow Watcher Hook (live file change events)             | ui     | P1       | UI-008                                 | done   |
| UI-015 | Rich Tool Call Rendering in Log Viewer                  | ui     | P1       | UI-006                                 | done   |
| UI-016 | Orchestrator Console in Run Detail                     | ui     | P1       | UI-011, ENGINE-015                     | done   |
| UI-017 | Edge Animation Persists After State Transition         | ui     | P0       | UI-004                                 | done   |
| UI-020 | Thinking label transitions to "Thoughts" when done     | ui     | P1       | UI-006                                 | todo   |
| UI-021 | Graph UI stuck on completion — requires manual re-select | ui     | P0       | UI-008, UI-011                         | todo   |
| UI-022 | Show cwd, task_dir, worktree in node details             | ui     | P1       | UI-005, ENGINE-025                     | todo   |
| UI-023 | Replace Flows list with selected flow detail view        | ui     | P1       | UI-010                                 | todo   |


### Phase 5 — Integration


| Issue      | Title                       | Domain | Priority | Depends On             | Status |
| ---------- | --------------------------- | ------ | -------- | ---------------------- | ------ |
| SERVER-009 | End-to-End Integration Test | server | P1       | SERVER-005, ENGINE-008 | done   |


### Phase 6 — Long-Lived Orchestrator Agents


| Issue      | Title                                              | Domain | Priority | Depends On              | Status |
| ---------- | -------------------------------------------------- | ------ | -------- | ----------------------- | ------ |
| ENGINE-012 | File Communication Protocol                        | engine | P0       | —                       | done   |
| ENGINE-013 | Orchestrator Prompt Template                       | engine | P0       | ENGINE-012              | done   |
| ENGINE-014 | Orchestrator Session Manager                       | engine | P0       | ENGINE-013              | done   |
| ENGINE-015 | Orchestrator as Task Executor                      | engine | P0       | ENGINE-012, ENGINE-014  | done   |
| ENGINE-016 | Orchestrator as Judge                              | engine | P0       | ENGINE-012, ENGINE-014  | done   |


### Phase 8 — Bug Fixes


| Issue      | Title                                                     | Domain | Priority | Depends On | Status      |
| ---------- | --------------------------------------------------------- | ------ | -------- | ---------- | ----------- |
| ENGINE-017 | Cancel triggers on_error=pause instead of cancelling      | engine | P1       | —          | done        |
| ENGINE-018 | Resume does not restart execution after pause             | engine | P2       | —          | done        |
| ENGINE-021 | Remove OrchestratorManager and simplify executor          | engine | P0       | —          | done        |
| ENGINE-023 | Implement self-report routing (DECISION.json)             | engine | P1       | DSL-007    | done        |
| ENGINE-024 | Emit executor activity logs visible in UI console        | engine | P1       | ENGINE-021 | todo        |
| ENGINE-025 | Workspace/data-dir separation + git worktree isolation   | engine | P0       | —          | done        |
| SERVER-010 | Update routes — remove orchestrator references            | server | P0       | ENGINE-021 | done        |


### Phase 7 — E2E Testing


| Issue   | Title                          | Domain | Priority | Depends On                   | Status |
| ------- | ------------------------------ | ------ | -------- | ---------------------------- | ------ |
| E2E-001 | Mock Subprocess Manager        | e2e    | P0       | ENGINE-001, ENGINE-005       | done   |
| E2E-002 | E2E Fixture Infrastructure     | e2e    | P0       | E2E-001, SERVER-001          | done   |
| E2E-003 | Test: Flow Library             | e2e    | P0       | E2E-002, UI-010, SERVER-002  | done   |
| E2E-004 | Test: Start Run                | e2e    | P0       | E2E-002, UI-012, SERVER-003  | done   |
| E2E-005 | Test: Run Detail               | e2e    | P0       | E2E-002, UI-011, SERVER-005  | done   |
| E2E-006 | Test: Flow Controls            | e2e    | P0       | E2E-005, UI-007, ENGINE-008  | done   |
| E2E-007 | Test: Failed Task              | e2e    | P0       | E2E-005, UI-007, ENGINE-008  | done   |
| E2E-008 | Test: Fork-Join                | e2e    | P0       | E2E-005, ENGINE-006          | done   |
| E2E-009 | Test: Conditional Branching    | e2e    | P0       | E2E-005, ENGINE-007          | done   |
| E2E-010 | Test: File Watcher             | e2e    | P0       | E2E-003, SERVER-006, UI-013  | done   |
| E2E-011 | Test: Cycles                   | e2e    | P1       | E2E-009                      | done   |
| E2E-012 | Test: Budget Warnings          | e2e    | P1       | E2E-005, ENGINE-002          | done   |
| E2E-013 | Test: WebSocket Reconnection   | e2e    | P1       | E2E-005, SERVER-005          | done   |
| E2E-014 | Test: Sidebar Navigation       | e2e    | P1       | E2E-003, UI-003              | done   |


---

## Cross-Domain Coordination

1. `**ast.py` is the shared contract (SHARED-001).** All Python domains import from `flowstate.dsl.ast`. Changes require coordination across DSL, state, engine, and server.
2. **Dependency direction: `dsl ← state ← engine ← server`.** Never import upstream.
3. **The UI is fully independent** — it communicates with the server only via REST API and WebSocket. No Python imports.
4. **REST API and WebSocket protocol** (specs.md Section 10) are the coupling between server and UI domains.

---

## How to Use This Plan

### For the Orchestrator Agent

1. **Read this file** to understand current state.
2. **Find ready issues**: scan the phase table for issues with status `todo` whose dependencies are all `done`.
3. **Group by domain**: collect ready DSL, state, engine, server, and UI issues separately.
4. **Handle shared issues** (e.g., SHARED-001) yourself — they produce artifacts all domains consume.
5. **Spawn domain agents in parallel**: one agent per domain with ready work. Each agent reads its issue file from `issues/<domain>/`, implements it, and reports completion.
6. **Verify completion**: When a domain agent reports done, verify by running the appropriate check skills (`/test`, `/lint`, `/check`).
7. **Mark done**: update the issue's Status field to `done` in both the issue file and this plan's phase table.
8. **Repeat** until all issues are done or no more issues are ready.

### For Domain Agents

1. **Read your issue file** (e.g., `issues/dsl/001-grammar.md`).
2. **Read `specs.md`** for full context.
3. **Read your agent spec** (e.g., `agents/01-dsl.md`) for module-level guidance.
4. **Implement** according to the acceptance criteria and technical design.
5. **Test** according to the testing strategy.
6. **Report completion** to the orchestrator.

### Status Values

- `todo` — not started
- `in_progress` — an agent is currently working on it
- `done` — implemented and verified
- `blocked` — waiting on a dependency or external input

