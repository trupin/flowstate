---
description: "Run the behavioral evaluator agent against completed issues. Tests the running application against specs like a skeptical real user."
argument-hint: "[ISSUE-ID|sprint-NNN|all]"
user_invocable: true
---

Invoke the evaluator agent to behaviorally verify completed work.

## Prerequisites

- The evaluator agent must be configured (`.claude/agents/evaluator.md` exists).
- The issue(s) must have passing `/test` and `/lint` before evaluation.
- The application must be startable via `uv run flowstate serve`.

## 1. Determine Scope

**By issue**: If `$ARGUMENTS` matches an issue ID (e.g., `SERVER-003`):
- Evaluate that single issue.

**By sprint**: If `$ARGUMENTS` matches `sprint-NNN`:
- Read the sprint contract at `issues/sprints/sprint-NNN.md`.
- Evaluate all issues listed in the contract.

**All recent**: If `$ARGUMENTS` is `all` or not provided:
- Find all issues with status `done` that do NOT have a corresponding eval file in `issues/evals/`.
- Evaluate each one.

## 2. Spawn the Evaluator

Use the Agent tool to spawn the evaluator agent (`.claude/agents/evaluator.md`).

Pass it:
- The issue ID(s) to evaluate.
- The sprint contract path (if evaluating a sprint).
- Instructions to read `specs.md` and the issue files for acceptance criteria.

If multiple issues are independent, you may spawn multiple evaluator agents in parallel.

## 3. Process Results

When the evaluator reports back:

**PASS**: Report success. The issue is ready for commit (or already committed).

**FAIL or PARTIAL**:
1. Read the eval verdict file at `issues/evals/<ISSUE-ID>-eval.md`.
2. Send the failures back to the appropriate domain agent for fixing.
3. After the domain agent fixes, re-run `/test` and `/lint`.
4. Re-spawn the evaluator for the same issue.
5. Repeat up to 3 iterations. If still failing after 3 attempts, escalate to the user with the eval file.

## 4. Report

After all evaluations complete:
- Which issues PASSED.
- Which issues FAILED and the eval file paths.
- How many iterations were needed for issues that eventually passed.

## Rules

- Never skip the evaluator for issues that have acceptance criteria in specs.md.
- Never commit code that has a FAIL verdict without user approval.
- The evaluator does not read source code -- do not send it code files.
- Respect the 3-iteration limit. Infinite loops waste resources and indicate an architectural problem.
