# [SHARED-011] Deployment docs in README + specs.md cross-ref

## Domain
shared

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: SHARED-010
- Blocks: —

## Spec References
- specs.md §13.3 Project Layout
- specs.md §13.4 Deployment & Installation

## Summary
Once the wheel exists and the code behaves correctly, the final piece is user-facing documentation: a concise "Install & first run" section in `README.md` that a new user can follow to go from nothing to a running Flowstate server in under a minute. Plus: cross-reference from the README to `specs.md §13.3/13.4` for anyone who wants the full spec.

## Acceptance Criteria
- [ ] `README.md` has a top-level "Install" section showing the pipx / uv tool commands:
  ```bash
  uv tool install flowstate
  # or
  pipx install flowstate
  ```
- [ ] `README.md` has a "Quickstart" section:
  ```bash
  cd ~/my-app
  flowstate init
  flowstate check flows/example.flow
  flowstate server
  # open http://127.0.0.1:9090
  ```
- [ ] `README.md` explains what `~/.flowstate/projects/<slug>/` contains in one short paragraph.
- [ ] `README.md` mentions that `pip install 'flowstate[lumon]'` is needed for sandboxed execution.
- [ ] `README.md` links to `specs.md §13` for the full deployment spec.
- [ ] `specs.md §13.4` has a back-link to the README Quickstart.
- [ ] A link check confirms both links resolve.

## Technical Design

### Files to Create/Modify
- `README.md` — add/replace the Install and Quickstart sections.
- `specs.md` — add the back-link in §13.4.

### Key Implementation Details
Keep the README content short and copy-pasteable. Do not duplicate spec content; link to it. The README is marketing + first-run; the spec is the contract.

Suggested README skeleton additions:
```markdown
## Install

Flowstate is a CLI + local web server. Install it with pipx or uv tool:

    uv tool install flowstate     # recommended
    # or
    pipx install flowstate

Sandboxed execution (optional):

    pip install 'flowstate[lumon]'

## Quickstart

    cd ~/my-app                # any existing project
    flowstate init             # creates flowstate.toml + flows/example.flow
    flowstate check flows/example.flow
    flowstate server           # http://127.0.0.1:9090

Flowstate stores all runtime data (database, run workspaces) under
`~/.flowstate/projects/<project-slug>/`. Your project directory is
never modified beyond the files created by `flowstate init`.

See [specs.md §13](./specs.md#13-configuration) for the full deployment
and project-layout spec.
```

### Edge Cases
- If the README already has unrelated Install sections (e.g., for dev contributors), keep those under a separate "Contributing" heading. End-user docs come first.
- Relative link from README to specs.md and from specs.md back to README should both work on GitHub's rendered markdown.

## Testing Strategy
Not applicable (docs). Verification is a human read-through + link check.

## E2E Verification Plan

### Verification Steps
1. Read README top-to-bottom as a first-time user. Follow the Quickstart literally: `uv tool install ...`, `cd ~/my-app`, `flowstate init`, `flowstate server`. Works without improvisation.
2. Click the README → specs.md link in a GitHub preview. It jumps to §13.
3. Click the specs.md → README back-link. It jumps to the Quickstart.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] README Install section written
- [ ] README Quickstart section written
- [ ] specs.md back-link added
- [ ] Links verified on GitHub render
- [ ] Fresh-user walkthrough succeeds
