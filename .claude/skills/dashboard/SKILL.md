---
description: "Quick project dashboard: issue counts, git state, what's actionable now. Use at the start of a session to orient."
user_invocable: true
---

Show a quick project status dashboard. This is the "where are we?" skill -- run it at the start of a session or when you need to orient.

## Gather Data

Collect all of the following in parallel (use multiple tool calls):

### A. Issue Status
Read `issues/PLAN.md` and count issues by status:
- `done` -- completed
- `in_progress` -- currently being worked on
- `todo` -- waiting (distinguish between **ready** (all deps done) and **blocked** (deps pending))
- `blocked` -- explicitly blocked with a reason

### B. Git State
Run these commands:
- `git status --short` -- any uncommitted changes? Untracked files?
- `git log --oneline -5` -- last 5 commits for context
- `git branch --show-current` -- what branch are we on?
- If not on main: `git log main..HEAD --oneline` -- how far ahead of main?

### C. Test Health (optional, fast-check only)
Check when tests were last run:
- Look at git log for the most recent commit message mentioning tests or a test-related issue.
- Do NOT run the full test suite -- that's what `/test` is for. Just report when tests were last verified.

## Present the Dashboard

Format as a compact summary:

```
## Project Status

**Branch**: main (clean | 3 uncommitted files)
**Last commit**: [ENGINE-005] Add budget enforcement -- 2h ago

### Issues
| Status      | Count |
|-------------|-------|
| Done        | 42    |
| In Progress | 2     |
| Ready       | 3     |
| Blocked     | 1     |
| Todo (deps) | 5     |

### Ready to Implement
- SERVER-018: Add pagination to runs endpoint (P1)
- UI-048: Add dark mode toggle (P1)
- ENGINE-012: Add retry logic for failed nodes (P2)

### In Progress
- DSL-015: Add import statement support (agent: dsl-dev)

### Blocked
- UI-050: Real-time log streaming -- waiting on SERVER-020

### Actionable Now
> Run `/implement next` to pick up SERVER-018 (highest priority ready issue).
> Or `/implement all` to run the full batch (3 ready issues across 2 domains).
```

## Rules

- Keep it fast. This should take seconds, not minutes. No deep exploration.
- Always end with an actionable suggestion -- what should the user do next?
- If the working tree is dirty, warn prominently: "**Warning: uncommitted changes from previous work. Commit or stash before starting new issues.**"
- If there are no ready issues and no in-progress issues, suggest `/decompose` or `/issue create` to queue up new work.
