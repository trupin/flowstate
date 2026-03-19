---
description: Pick ready issues from the plan and implement them by spawning the appropriate domain agent. This is the main orchestration skill.
argument-hint: "[issue-id, 'next', or 'all']"
user_invocable: true
---

Implement one or more issues from the Flowstate project plan.

## 1. Read the current plan

Read `issues/PLAN.md` to understand the current state of all issues.

## 2. Determine what to implement

If `$ARGUMENTS` is a specific issue ID (e.g., `DSL-001` or `ENGINE-005`):
- Read that issue file from `issues/dsl/` or `issues/state/` or `issues/engine/` or `issues/server/` or `issues/ui/` or `issues/shared/`.
- Verify its dependencies are all `done`. If not, report which dependencies are blocking and stop.

If `$ARGUMENTS` is `next` or not provided:
- Scan the phase table for all issues with status `todo`.
- Filter to those whose dependencies are all `done`.
- Group by domain (dsl, state, engine, server, ui, shared).
- Pick the highest-priority ready issues (P0 before P1 before P2).

If `$ARGUMENTS` is `all`:
- This runs the full orchestration loop continuously until no more issues can be picked up.
- Follow steps 3–5 as normal for each batch. After each batch completes (all agents done, verified, committed), **loop back to step 1**: re-read `issues/PLAN.md`, find newly unblocked issues, and repeat.
- Stop only when there are no `todo` issues with all dependencies `done` (i.e., nothing is ready).
- If an issue is marked `blocked`, skip it and continue with other ready issues. Do not stop the loop because of a blocked issue.

## 2b. Spec validation

Before implementing any issue, read the relevant section(s) of `specs.md` for that issue. Look for **spec holes** — anything the issue's acceptance criteria or technical design requires but the spec doesn't define clearly enough to implement with confidence. Examples: undefined edge cases, missing error behavior, ambiguous data formats, unspecified interaction between components, missing enum variants.

**If the spec hole is blocking** (you cannot implement the issue without an answer):
- Stop implementation for that issue.
- Present the spec gap to the user with:
  - What is missing or ambiguous.
  - Why it blocks implementation.
  - 2–3 concrete alternative approaches, each with trade-offs, so the user can pick one.
- Do not guess or make assumptions. Wait for the user's choice before proceeding.
- Other non-blocked issues can continue in parallel.

**If the spec hole is non-blocking** (you can implement the issue with a reasonable default, but the gap should be addressed later):
- Proceed with implementation using the most reasonable interpretation.
- At the end (in step 6), file a new issue in `issues/` for each non-blocking spec gap found:
  - Title: `Clarify spec: <what's missing>`
  - Priority: P1
  - Add it to `issues/PLAN.md` in an appropriate phase.
- Note the assumption you made in the commit message or issue file so it's traceable.

## 3. Handle shared issues directly

If the ready issue is in `shared/` (e.g., SHARED-001):
- Read the issue file.
- Implement it yourself as the orchestrator.
- Run any validation specified in the Testing Strategy.
- Update the issue status to `done` in both the issue file and `issues/PLAN.md`.

## 4. Spawn domain agents for domain issues

For DSL issues:
- Use the Agent tool to spawn the `dsl-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, and report back.

For state issues:
- Use the Agent tool to spawn the `state-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, and report back.

For engine issues:
- Use the Agent tool to spawn the `engine-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, and report back.

For server issues:
- Use the Agent tool to spawn the `server-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, and report back.

For UI issues:
- Use the Agent tool to spawn the `ui-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, and report back.

If multiple domains have ready issues, spawn all agents in parallel (they work on independent domains).

## 5. Verify, commit, and update

When a domain agent reports completion:
1. Use `/simplify` on the code.
2. Run the appropriate check skill (`/test`, `/lint`, `/check`) to verify.
3. If checks fail, report the failures to the domain agent for fixing. Loop until checks pass.
4. If checks pass, **commit the changes** for this issue:
   - Stage only the files relevant to this issue (`git add <specific files>`). Never `git add -A` or `git add .`.
   - Create one commit per issue with the message format:
     ```
     [ISSUE-ID] Short imperative description

     - Key change 1
     - Key change 2

     Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
     ```
     Example: `[DSL-001] Add Lark grammar definition`
5. Update the issue's Status to `done` in:
   - The issue file (change `todo` to `done`)
   - `issues/PLAN.md` (update the status column in the phase table)

If multiple domain agents complete in sequence, commit each issue separately before moving to the next.

## 6. Report

After all targeted issues are processed, report:
- Which issues were completed.
- Which issues failed and why.
- What the next ready issues are (for the next `/implement` invocation).

## Rules

- Never skip the dependency check. Implementing an issue with unmet dependencies will produce broken code.
- Never modify another domain's code directly. Spawn the appropriate agent.
- Mark issues as `in_progress` before spawning agents, and `done` only after verification.
- If an agent cannot complete an issue, mark it as `blocked` with a reason.
