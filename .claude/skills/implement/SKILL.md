---
description: Pick ready issues from the plan and implement them by spawning the appropriate domain agent. This is the main orchestration skill.
argument-hint: "[issue-id, 'next', or 'all']"
user_invocable: true
---

Implement one or more issues from the Flowstate project plan.

## 0. Restore context (if context-manager active)

If `.claude/agents/context-manager.md` exists and `.claude/handoffs/` contains files, spawn the context-manager agent in "restore" mode to produce a session briefing. Use this briefing to orient before reading the plan.

Skip this step if no handoff files exist or if this is a continuation of an active session.

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
- Follow steps 3-6 as normal for each batch. After each batch completes (all agents done, verified, committed), **loop back to step 1**: re-read `issues/PLAN.md`, find newly unblocked issues, and repeat.
- Stop only when there are no `todo` issues with all dependencies `done` (i.e., nothing is ready).
- If an issue is marked `blocked`, skip it and continue with other ready issues. Do not stop the loop because of a blocked issue.

## 2b. Spec validation

Before implementing any issue, read the relevant section(s) of `specs.md` for that issue. Look for **spec holes** -- anything the issue's acceptance criteria or technical design requires but the spec doesn't define clearly enough to implement with confidence.

**If the spec hole is blocking** (you cannot implement the issue without an answer):
- Stop implementation for that issue.
- Present the spec gap to the user with:
  - What is missing or ambiguous.
  - Why it blocks implementation.
  - 2-3 concrete alternative approaches, each with trade-offs, so the user can pick one.
- Do not guess or make assumptions. Wait for the user's choice before proceeding.
- Other non-blocked issues can continue in parallel.

**If the spec hole is non-blocking** (you can implement the issue with a reasonable default, but the gap should be addressed later):
- Proceed with implementation using the most reasonable interpretation.
- At the end (in step 7), file a new issue in `issues/` for each non-blocking spec gap found:
  - Title: `Clarify spec: <what's missing>`
  - Priority: P1
  - Add it to `issues/PLAN.md` in an appropriate phase.
- Note the assumption you made in the commit message or issue file so it's traceable.

## 3. Sprint contract (if evaluator active)

If `.claude/agents/evaluator.md` exists AND the batch has more than 1 issue:
1. Spawn the sprint-planner agent (`.claude/agents/sprint-planner.md`).
2. Pass it the list of ready issue IDs.
3. It produces a sprint contract at `issues/sprints/sprint-NNN.md`.

Skip this step for single issues or trivial batches (P2, single-file changes).

## 4. Handle shared issues directly

If the ready issue is in `shared/` (e.g., SHARED-001):
- Read the issue file.
- Implement it yourself as the orchestrator.
- Run any validation specified in the Testing Strategy.
- Update the issue status to `done` in both the issue file and `issues/PLAN.md`.

## 5. Spawn domain agents for domain issues

For DSL issues:
- Use the Agent tool to spawn the `dsl-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, verify E2E, and report back.

For state issues:
- Use the Agent tool to spawn the `state-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, verify E2E, and report back.

For engine issues:
- Use the Agent tool to spawn the `engine-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, verify E2E, and report back.

For server issues:
- Use the Agent tool to spawn the `server-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, verify E2E, and report back.

For UI issues:
- Use the Agent tool to spawn the `ui-dev` agent.
- Pass the issue ID(s) and instruct it to read the issue file, implement, test, verify E2E, and report back.

If a sprint contract was produced in step 3, include the contract file path so the domain agent knows what the evaluator will verify.

If multiple domains have ready issues, spawn all agents in parallel (they work on independent domains).

## 6. Verify, evaluate, and commit

When a domain agent reports completion:

1. **Simplify**: Use `/simplify` on the changed code.
2. **Run checks**: `/test` and `/lint` to verify correctness. If checks fail, report failures to the domain agent for fixing. Loop until checks pass.
3. **Evaluate** (if evaluator active): If `.claude/agents/evaluator.md` exists:
   - Spawn the evaluator agent for this issue (or the sprint batch).
   - The evaluator will audit the E2E proof-of-work for credibility and completeness.
   - If FAIL: send the eval verdict file (`issues/evals/<ISSUE-ID>-eval.md`) to the domain agent for fixing. After fixes, re-run `/test` + `/lint`, then re-evaluate.
   - Loop up to 3 iterations. If still failing after 3 attempts, escalate to user with the eval file.
   - If PASS: proceed to audit.
   If evaluator is not active: spot-check that the "E2E Verification Log" section is present and not placeholder text. If missing, send back to the domain agent.
4. **Decide whether to audit**: Run `/audit <ISSUE-ID>` only when:
   - The issue is P0 (critical path)
   - The issue touches shared contracts or cross-domain interfaces
   - The issue modifies more than ~5 files
   - The issue involves security-sensitive code (auth, input validation, crypto)
   - Skip `/audit` for small, single-file changes, documentation-only issues, and P2 tasks.
5. If the audit surfaces FIX items, send them back to the domain agent. Loop until clean.
6. **Commit the changes**:
   - Stage only the files relevant to this issue (`git add <specific files>`). Never `git add -A` or `git add .`.
   - Create one commit per issue with the message format:
     ```
     [ISSUE-ID] Short imperative description

     - Key change 1
     - Key change 2

     Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
     ```
7. Update the issue's Status to `done` in both the issue file and `issues/PLAN.md`.

If multiple domain agents complete in sequence, commit each issue separately before moving to the next.

## 7. Checkpoint and report

1. **Checkpoint** (if context-manager active): After every 3-5 completed issues, spawn the context-manager in "checkpoint" mode to create a handoff artifact at `.claude/handoffs/`.

2. **Report**: After all targeted issues are processed, report:
   - Which issues were completed.
   - Which issues failed and why (include eval verdict paths if applicable).
   - What the next ready issues are (for the next `/implement` invocation).

## Rules

- Never skip the dependency check. Implementing an issue with unmet dependencies will produce broken code.
- Never modify another domain's code directly. Spawn the appropriate agent.
- Mark issues as `in_progress` before spawning agents, and `done` only after verification.
- If an agent cannot complete an issue, mark it as `blocked` with a reason.
- Never commit code with a FAIL evaluator verdict without explicit user approval.
- Respect the 3-iteration evaluator loop limit -- escalate to user after that.
- The evaluator agent must never receive source code files -- only issue IDs and sprint contract paths.
