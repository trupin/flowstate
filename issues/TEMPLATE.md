# [DOMAIN-NNN] Title

## Domain
dsl | state | engine | server | ui | shared

## Status
todo | in_progress | done | blocked

## Priority
P0 (critical path) | P1 (important) | P2 (nice-to-have)

## Dependencies
- Depends on: [DOMAIN-NNN], ...
- Blocks: [DOMAIN-NNN], ...

## Spec References
- specs.md Section N — "Section Title"

## Summary
One paragraph: what this accomplishes and why.

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Technical Design

### Files to Create/Modify
- `path/to/file.ext` — purpose

### Key Implementation Details
Enough detail for an autonomous agent to implement without asking questions.

### Edge Cases
- ...

## Testing Strategy
How to verify this works.

## E2E Verification Plan
How to verify this against the **real running application** — no mocks, no test
clients, no in-memory databases. Start the actual server, hit real HTTP endpoints,
open a real browser with Playwright if UI is involved. For bugs, include
reproduction steps. For features, describe how to exercise the feature end-to-end.

### Reproduction Steps (bugs only)
1. Start server: `uv run flowstate server --port 9090`
2. [steps to trigger the bug]
3. Expected: [what should happen]
4. Actual: [what goes wrong]

### Verification Steps
1. [steps to verify the fix/feature against the running app]
2. Expected: [what should happen after implementation]

## E2E Verification Log
_Filled in by the implementing agent as proof-of-work. Must be from real E2E
testing — no mocks, no test clients. Real server, real HTTP requests, real
browser. Include specific commands run, actual outputs observed, and pass/fail
conclusions._

### Reproduction (bugs only)
_[Agent fills this in: exact commands, observed output, confirmation bug exists]_

### Post-Implementation Verification
_[Agent fills this in: server restarted, exact commands, observed output, confirmation fix/feature works]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
