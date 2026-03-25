---
description: "Manage Flowstate issues: create, close, implement, plan, refine, list, show"
user_invocable: true
---

Manage Flowstate issues. Parse the subcommand from the first argument and the body from the rest.

**Usage:** `/issue <subcommand> <body>`

## Subcommands

### `create <description>`

Create a new issue from a natural language description. **Thorough research and design thinking come first — filing comes last.**

#### Phase 1: Deep Research

Before writing any issue file, investigate the codebase to understand the current state and surface design questions:

1. **Explore the affected code.** Use the Explore agent (or Glob/Grep directly) to read every file the feature will touch. Understand the current data flow, state management, component boundaries, and protocols. Do not rely on memory or assumptions — read the code.
2. **Identify cross-domain impact.** Trace the change through the dependency chain (`dsl ← state ← engine ← server ← UI`). If a feature requires work in multiple domains, plan separate issues for each domain with explicit dependency links. Favor parallelizable issues — if two domains can be worked independently, structure them that way.
3. **Assess regression risk.** Identify what existing functionality could break. Run the existing test suite (`uv run pytest` for Python, `cd ui && npm run build` for UI) to establish a green baseline before filing. Note any fragile areas in the issue's Edge Cases section.
4. **Check for prior art.** Search the codebase for similar patterns, existing utilities, or partially-implemented versions of the feature. Reference them in the Technical Design so the implementer doesn't reinvent.

#### Phase 2: Ask Clarifying Questions

Before filing, surface design blind spots and ambiguous boundaries. Ask the user about:

- **Behavioral ambiguities**: "When X happens during Y, should the system do A or B?" Present concrete scenarios, not abstract questions.
- **Scope boundaries**: "Should this also handle [related edge case], or is that a separate issue?"
- **UX expectations**: "What should the user see when [error/edge case]? Should it be silent, a toast, or an inline error?"
- **Priority tradeoffs**: "This could be done simply with [approach A] or more robustly with [approach B] — which do you prefer for now?"

Do not file issues with ambiguous acceptance criteria. If you can't define "done" precisely, you need to ask more questions first. However, don't over-ask — if a reasonable default exists and the user's intent is clear, state your assumption and proceed.

#### Phase 3: Plan the Issue Structure

For complex features that span multiple domains or have independent sub-tasks:

- **Create multiple issues** rather than one monolithic issue. Each issue should be implementable by a single domain agent in one session.
- **Maximize parallelism**: structure dependencies so that as many issues as possible can be worked concurrently. If engine and UI work are independent, don't make one block the other unnecessarily.
- **Group into a phase**: add all related issues under a new phase heading in `issues/PLAN.md` so they're visually grouped.

For simple, single-domain changes: one issue is fine.

#### Phase 4: Consider Testing Strategy

Every issue must have a concrete testing strategy. Think through:

- **Unit tests**: what functions/components need test coverage?
- **Integration tests**: does the feature cross module boundaries that need integration testing?
- **E2E / UI testing**: if the feature has user-visible behavior, describe how to verify it works end-to-end. Consider whether a Playwright E2E test is warranted (especially for features involving WebSocket state, real-time updates, or multi-step user flows). Reference existing E2E patterns in `tests/e2e/` if applicable.
- **Regression surface**: list specific existing tests that should still pass, or areas to manually verify haven't broken.

#### Phase 5: Update the Spec

`specs.md` is the source of truth for all behavior. If the new feature introduces behavior not yet covered by the spec, or modifies existing specified behavior, **update `specs.md` as part of filing the issue**:

1. **Read the relevant spec section(s)** referenced by the feature.
2. **Add or update spec text** to describe the new behavior, API endpoints, data formats, UI interactions, or protocol changes the feature introduces.
3. **Keep it concise** — spec entries should define the contract (what), not the implementation (how). The issue's Technical Design covers the how.
4. **If the feature is exploratory** and the exact behavior will be determined during implementation, add a stub section in the spec with a `[TBD: <issue-id>]` marker so it's clear the spec needs updating once the feature lands.

This ensures the spec stays current and domain agents implementing the issue can reference the spec for authoritative behavior definitions.

#### Phase 6: File the Issue(s)

Now create the actual issue file(s):

1. **Determine the domain** from the description: `dsl`, `state`, `engine`, `server`, `ui`, or `shared`.
2. **Find the next issue number** by scanning `issues/<domain>/` for the highest NNN prefix and incrementing.
3. **Generate a short slug** from the description (e.g., "add retry logic" → `add-retry-logic`).
4. **Create the issue file** at `issues/<domain>/NNN-<slug>.md` using the template at `issues/TEMPLATE.md`.
5. **Fill in all sections** based on the research:
   - Title: `[DOMAIN-NNN] <imperative title>`
   - Domain, Status (`todo`), Priority (infer from description, default P1)
   - Dependencies: infer from domain and description, or leave as `none`
   - Summary: expand the description into a clear paragraph
   - Acceptance Criteria: derive 3-5 testable criteria — each must be unambiguous and verifiable
   - Technical Design: identify files to modify, key implementation details, and references to existing patterns discovered during research
   - Edge Cases: include regression risks identified during research
   - Testing Strategy: concrete test plan including unit, integration, and E2E considerations
6. **Add the issue to `issues/PLAN.md`** in the appropriate phase table. If no existing phase fits, create a new one. Use `todo` status.
7. **File follow-up issues for shortcuts.** If the Technical Design takes a simpler/faster approach for POC or short-term purposes (e.g., stubbing with `NotImplementedError`, skipping error handling, hardcoding values, omitting edge cases, using `any`/loose types), **always create a follow-up issue** for each shortcut. Title it `Harden: <what was cut>`, set priority P2, and link it as "Depends on" the current issue. Add it to the plan. This ensures tech debt is tracked and never silently accumulated.
8. **Report** the created issue path(s) and ID(s), along with any assumptions made, shortcuts taken, and follow-up issues filed.

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
