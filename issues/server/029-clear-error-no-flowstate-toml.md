# [SERVER-029] Clear error when no `flowstate.toml` found

## Domain
server

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SERVER-026
- Blocks: —

## Spec References
- specs.md §13.3 Project Layout

## Summary
When any CLI command fails to resolve a project (no `flowstate.toml` in CWD or any ancestor), it currently either crashes with a bare traceback or emits a terse error. This issue wraps the `resolve_project()` call in every CLI command with a user-friendly error message pointing at `flowstate init`.

## Acceptance Criteria
- [ ] Every CLI command that requires a project catches `ProjectNotFoundError` and prints:
  ```
  No flowstate.toml found in <cwd> or any parent directory.
  Run `flowstate init` to create one, or cd into a Flowstate project.
  ```
  (The `<cwd>` is the actual CWD, resolved to absolute.)
- [ ] Exit code is 2 (not 1) for "not a project" — conventional meaning of "usage error / context error", distinct from runtime failures.
- [ ] The traceback is suppressed (stderr only shows the message).
- [ ] Commands that don't require a project (`flowstate --version`, `flowstate --help`, `flowstate init`) are unaffected and still work outside any project.
- [ ] Unit tests verify exit code and error message for each command.

## Technical Design

### Files to Create/Modify
- `src/flowstate/cli.py` — factor the `resolve_project()` + error handling into a single helper; call it from every command.
- `tests/server/test_cli_errors.py` — new tests.

### Key Implementation Details
```python
def _require_project() -> Project:
    try:
        return resolve_project()
    except ProjectNotFoundError:
        cwd = Path.cwd().resolve()
        typer.echo(
            f"No flowstate.toml found in {cwd} or any parent directory.\n"
            f"Run `flowstate init` to create one, or cd into a Flowstate project.",
            err=True,
        )
        raise typer.Exit(code=2)
```

Every command (`server`, `run`, `check`, `runs`, `status`, `schedules`, `trigger`) calls `project = _require_project()` at the top. The `init` command must **not** call this — it creates the project.

### Edge Cases
- `FLOWSTATE_CONFIG` set but pointing at a non-existent file → `resolve_project()` already raises with a specific message; still route through the helper so exit code is consistent (2).
- The walk-up finds a `flowstate.toml` that fails to parse → that's a different error (`FlowstateConfigError`), exit code 2 as well, with the underlying parse error in the message.

## Testing Strategy
- Typer `CliRunner` tests:
  - Each project-requiring command run in a `tmp_path` with no anchor → exit 2, stderr contains "No flowstate.toml found".
  - Same commands run in a `tmp_path` with an empty `flowstate.toml` → succeed (or fail for other valid reasons, but not with the "not found" error).
  - `flowstate init` in a `tmp_path` with no anchor → exit 0.
  - `flowstate --version` → exit 0 regardless.

## E2E Verification Plan

### Verification Steps
1. `cd / && flowstate server` → exits 2, prints the clear message, no traceback.
2. `cd / && flowstate run /tmp/foo.flow` → same.
3. `cd / && flowstate init` → creates project successfully (if CWD writable) — note: running in `/` is obviously unusual; the intent is that `init` bypasses the check.
4. `cd / && flowstate --help` → shows help, exit 0.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `_require_project()` helper implemented
- [ ] Every project-requiring command migrated
- [ ] `init` command bypasses the check
- [ ] Exit code 2 used
- [ ] Traceback suppressed
- [ ] Unit tests passing
- [ ] `/lint` passes
- [ ] E2E steps above verified
