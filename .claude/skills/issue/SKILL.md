---
description: "Manage Flowstate issues: create, close, implement, plan, refine, list, show"
user_invocable: true
---

Manage Flowstate issues. Parse the subcommand from the first argument and the body from the rest.

**Usage:** `/issue <subcommand> <body>`

## Subcommands

### `create <description>`

Create a new issue from a natural language description.

1. **Determine the domain** from the description: `dsl`, `state`, `engine`, `server`, `ui`, or `shared`.
2. **Find the next issue number** by scanning `issues/<domain>/` for the highest NNN prefix and incrementing.
3. **Generate a short slug** from the description (e.g., "add retry logic" → `add-retry-logic`).
4. **Create the issue file** at `issues/<domain>/NNN-<slug>.md` using the template at `issues/TEMPLATE.md`.
5. **Fill in all sections** based on the description:
   - Title: `[DOMAIN-NNN] <imperative title>`
   - Domain, Status (`todo`), Priority (infer from description, default P1)
   - Dependencies: infer from domain and description, or leave as `none`
   - Summary: expand the description into a clear paragraph
   - Acceptance Criteria: derive 3-5 testable criteria from the description
   - Technical Design: identify files to modify and key implementation details
   - Testing Strategy: suggest test approach
6. **Add the issue to `issues/PLAN.md`** in the appropriate phase table. If no existing phase fits, create a new one. Use `todo` status.
7. **Report** the created issue path and ID.

### `close <issue-id>`

Mark an issue as done.

1. **Find the issue file** matching `<issue-id>` (e.g., `ENGINE-019` → `issues/engine/019-*.md`).
2. **Update the Status field** in the issue file from its current value to `done`.
3. **Update `issues/PLAN.md`** — find the row for this issue and change its status to `done`.
4. **Report** what was closed.

### `implement <issue-id>`

Pick an issue and implement it using the appropriate domain agent. This delegates to the `/implement` skill logic.

1. **Read the issue file** for the given ID.
2. **Determine the domain** from the issue file.
3. **Spawn the appropriate domain agent** (dsl-dev, state-dev, engine-dev, server-dev, ui-dev) with the issue context.
4. **After the agent completes**, verify with `/test` and `/lint`.
5. **Mark the issue as done** (same as `close`).

### `plan`

Show the current state of the plan — what's done, what's in progress, what's ready to implement.

1. **Read `issues/PLAN.md`**.
2. **Summarize** in a compact format:
   - Total issues by status (done, in_progress, todo, blocked)
   - Ready issues (status=todo, all dependencies=done)
   - In-progress issues
   - Blocked issues with what they're waiting on

### `refine <issue-id>`

Refine an existing issue — flesh out missing details, improve acceptance criteria, or update the technical design.

1. **Read the issue file** for the given ID.
2. **Read the relevant source files** mentioned in the Technical Design section to understand current code.
3. **Read `specs.md`** for the relevant section if Spec References are listed.
4. **Update the issue file** with:
   - More specific acceptance criteria (if vague)
   - Concrete file paths and line numbers in Technical Design
   - Edge cases identified from reading the code
   - Updated dependencies if new ones are discovered
5. **Report** what was refined and any concerns found.

### `list [--domain <domain>] [--status <status>]`

List issues, optionally filtered by domain or status.

1. **Scan `issues/PLAN.md`** for the phase tables.
2. **Filter** by domain and/or status if flags are provided.
3. **Display** as a compact table: `ID | Title | Status | Depends On`.

### `show <issue-id>`

Show the full contents of an issue.

1. **Find the issue file** matching `<issue-id>`.
2. **Display** its full contents.

### `reopen <issue-id>`

Reopen a closed issue by setting its status back to `todo`.

1. **Find the issue file** and update Status to `todo`.
2. **Update `issues/PLAN.md`** to reflect the change.

## Issue ID Resolution

Issue IDs can be provided in several formats:
- Full: `ENGINE-019`
- Lowercase: `engine-019`
- Number only (requires domain context): `019`

Resolution logic:
```
1. Normalize to uppercase: ENGINE-019
2. Extract domain prefix: ENGINE → engine
3. Extract number: 019
4. Glob for: issues/<domain>/<number>-*.md
5. If exactly one match, use it. If zero or multiple, report error.
```

## File Locations

- Issue files: `issues/<domain>/NNN-<slug>.md`
- Plan: `issues/PLAN.md`
- Template: `issues/TEMPLATE.md`
- Domains: `dsl`, `state`, `engine`, `server`, `ui`, `shared`, `e2e`

## Notes

- Issue numbers are per-domain (DSL-007 and ENGINE-007 are different issues).
- Always use the template format from `issues/TEMPLATE.md`.
- When creating issues, read the relevant codebase files to produce accurate Technical Design sections.
- The plan file has a 200-line soft limit — keep entries concise.
