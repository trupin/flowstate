# Issue Filing Procedure

When a bug is found during E2E testing, file an issue and propose a fix plan.

## Steps

### 1. Determine the domain

Based on the nature of the bug:
- **Subprocess not killed / stuck task** → `engine`
- **UI not updating / rendering issues** → `ui`
- **API returning wrong status / data** → `server`
- **Flow not completing / wrong transitions** → `engine`
- **Judge making unparseable decisions** → `engine`
- **WebSocket events not arriving** → `server`
- **File watcher not detecting changes** → `server`

### 2. Find the next issue number

List existing issues in the domain directory:

```bash
ls issues/{domain}/ | sort -t'-' -k1 -n | tail -1
```

Increment the number for the new issue. Use the format `NNN-description.md` (e.g., `017-cancel-subprocess-orphan.md`).

### 3. Create the issue file

Use `issues/TEMPLATE.md` format. Fill in:

```markdown
# [{DOMAIN}-{NNN}] {Title}

## Domain
{domain}

## Status
todo

## Priority
P1

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section {relevant_section}

## Summary
Found during E2E testing (suite: {suite_name}).

{Description of what was expected vs what happened.}

## Acceptance Criteria
- [ ] {The fix criteria — what should happen instead}
- [ ] Verified by re-running `/e2e {suite_name}`

## Technical Design

### Files to Create/Modify
- `{path/to/likely/file}` — {what needs to change}

### Key Implementation Details
{Root cause analysis and proposed fix approach}

### Edge Cases
- {Any edge cases discovered during the E2E test}

## Testing Strategy
Re-run `/e2e {suite_name}` and verify the suite passes.

## Evidence
- Screenshot: {path if available}
- Logs: {relevant log excerpt}
- Suite: {suite_name}
- Wall time at failure: {time}
```

### 4. Add to PLAN.md

Add the new issue to `issues/PLAN.md` in a new "Phase 8 — E2E Bug Fixes" section (create it if it doesn't exist).

### 5. Propose a fix plan

Do NOT fix the bug immediately. Instead, provide:

1. **Likely root cause** — what code path is failing and why
2. **Files to change** — specific files and functions
3. **Fix approach** — what the fix would look like
4. **Risks** — any side effects or dependencies
5. **Testing** — how to verify the fix works

Include this fix plan in the issue's Technical Design section and in the final E2E summary.
