---
description: "Decompose a high-level feature into a phased set of issues across domains with dependencies. Strategic planning for multi-issue work."
argument-hint: "<feature description>"
user_invocable: true
---

Take a high-level feature description and break it into a structured, implementable plan of issues across domains.

This is different from `/issue create` (which files one issue after deep research). `/decompose` does **strategic decomposition**: "I want feature X" becomes 5-15 issues across multiple domains in 2-3 phases with a dependency graph.

## 1. Understand the Feature

Read `$ARGUMENTS` as a feature description. If it's vague, ask one round of clarifying questions focused on:
- What's the user-visible outcome?
- What's the scope boundary? (What's explicitly NOT included?)
- Any hard constraints? (must use X, can't change Y, deadline Z)

If there's a `specs.md`, check whether this feature is already specified or conflicts with existing specs.

## 2. Map the Impact

Use the Explore agent to understand the current codebase state relevant to this feature:

1. **Trace the data flow**: How does data enter, transform, persist, and surface for this feature?
2. **Identify every domain touched**: Which of the 5 domains (DSL, state, engine, server, UI) need changes? Which are untouched?
3. **Find the seams**: Where are the interfaces between domains that this feature crosses? Check the shared AST contract in `src/flowstate/dsl/ast.py`.
4. **Spot prerequisites**: Does this need schema changes, new shared types, or config before domain work can start?

## 3. Design the Issue Graph

Decompose the feature into issues following these principles:

- **One issue = one domain agent = one session of work.** If an issue requires two domain agents, split it.
- **Shared-first**: Issues that create shared contracts (AST types, schemas, interfaces) come first as SHARED-* issues in Phase 0 of the feature.
- **Maximize parallelism**: Structure dependencies so the maximum number of issues can run concurrently. Draw the dependency graph mentally -- the widest point is your throughput.
- **No orphans**: Every issue must connect to at least one other via dependency or phase grouping.
- **Progressive delivery**: Order phases so that after each phase, something testable or demonstrable exists.

## 4. Draft the Plan

For each issue, determine:
- **ID**: `DOMAIN-NNN` (use the next available number per domain: DSL-*, STATE-*, ENGINE-*, SERVER-*, UI-*, SHARED-*)
- **Title**: Imperative, specific (e.g., "Add WebSocket event types for live updates", not "WebSocket stuff")
- **Domain**: Which domain and agent
- **Priority**: P0 for critical path, P1 for important, P2 for polish
- **Dependencies**: Which issues must complete first
- **Size estimate**: S (< 1 file), M (2-5 files), L (5+ files or new subsystem)

Organize into phases:
- **Phase N+0**: Shared contracts and prerequisites
- **Phase N+1**: Core domain implementations (maximize parallel issues here)
- **Phase N+2**: Integration, cross-domain wiring, UI
- **Phase N+3**: Polish, edge cases, hardening (P2 issues)

## 5. Present for Approval

Show the user the plan as a table:

```
## Feature: <name>

### Phase N -- <title>
| ID | Title | Domain | Size | Priority | Depends On |
|----|-------|--------|------|----------|------------|
| ...| ...   | ...    | ...  | ...      | ...        |

### Phase N+1 -- <title>
| ...
```

Below the table, show:
- **Parallelism**: "Phase N+1 has 4 issues across 3 domains -- all can run concurrently after Phase N."
- **Total scope**: "X issues, estimated Y agent sessions"
- **Risks**: Any spec gaps, architectural unknowns, or areas where the plan might need adjustment during implementation.

Ask: "Does this breakdown look right? Anything to add, remove, or re-scope?"

**Wait for user approval before proceeding.** Iterate on the plan if the user requests changes.

## 6. File Everything

Once the user approves:

1. **Update `specs.md`** if the feature introduces new behavior (add stubs with `[TBD: ISSUE-ID]` markers).
2. **Create all issue files** in `issues/<domain>/NNN-<slug>.md` using `issues/TEMPLATE.md`. For each issue, fill in all sections -- the whole point of `/decompose` is that the issues are ready to implement immediately.
3. **Add a new phase section** to `issues/PLAN.md` with all the issues.
4. **Report** the full list of created issues and suggest: "Run `/implement next` to start, or `/implement all` to run the full plan."

## Rules

- Don't create issues the user didn't approve. Always present before filing.
- Don't create monolith issues. If an issue description feels like it needs sub-headings in the Technical Design, it should be split.
- Don't under-plan: every issue must have enough Technical Design detail for a domain agent to implement without asking questions.
- Don't over-plan: if the feature is simple enough for 1-2 issues, say so and suggest `/issue create` instead.
