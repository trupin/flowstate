# [SERVER-028] `flowstate init` command with project-type detection

## Domain
server

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SERVER-026, SERVER-027
- Blocks: —

## Spec References
- specs.md §13.2 CLI Interface
- specs.md §13.3 Project Layout

## Summary
Add a `flowstate init` subcommand that bootstraps a new Flowstate project inside an existing user repo. It writes a minimal `flowstate.toml`, creates `flows/`, and seeds an example `.flow` file whose content is tailored to the detected project type (Node / Python / Rust / generic). This is the first thing a pipx-installed user does after `uv tool install flowstate`, so it has to just work.

## Acceptance Criteria
- [ ] `flowstate init` creates `./flowstate.toml` with sane defaults: `host = "127.0.0.1"`, `port = 9090`, `watch_dir = "flows"`, plus commented-out harness examples.
- [ ] `flowstate init` creates `./flows/` if missing.
- [ ] `flowstate init` writes `./flows/example.flow` with content picked by project-type detection:
  - `package.json` present → Node-flavored `install → build → test` flow
  - `pyproject.toml` present → Python `install → lint → test` flow
  - `Cargo.toml` present → Rust `build → test` flow
  - None of the above → generic "hello flowstate" single-node flow
- [ ] If `flowstate.toml` already exists, `flowstate init` refuses to run unless `--force` is passed, in which case it overwrites `flowstate.toml` only (never overwrites existing flow files).
- [ ] If `flows/example.flow` already exists, it is not overwritten — the command prints a note and continues.
- [ ] After success, the command prints a "next steps" message:
  ```
  Created flowstate.toml and flows/example.flow.
  Next:
    flowstate check flows/example.flow
    flowstate server
  ```
- [ ] Exit code is 0 on success, non-zero on any failure (including "file exists without --force").

## Technical Design

### Files to Create/Modify
- `src/flowstate/cli.py` — new `init` command.
- `src/flowstate/init_templates/` — new package directory containing the four starter `.flow` templates and the `flowstate.toml` template. Bundled via `[tool.hatch.build.targets.wheel]` so they ship in the wheel.
- `tests/server/test_cli_init.py` — new test file.

### Key Implementation Details
```python
import typer
from pathlib import Path
from importlib.resources import files

app = typer.Typer()

@app.command()
def init(force: bool = typer.Option(False, "--force", help="Overwrite existing flowstate.toml")) -> None:
    cwd = Path.cwd()
    toml_path = cwd / "flowstate.toml"
    flows_dir = cwd / "flows"
    example_path = flows_dir / "example.flow"

    if toml_path.exists() and not force:
        typer.echo(f"flowstate.toml already exists at {toml_path}. Use --force to overwrite.", err=True)
        raise typer.Exit(code=1)

    project_type = _detect_project_type(cwd)
    toml_content = _render_toml_template()
    example_content = _render_example_template(project_type)

    toml_path.write_text(toml_content)
    flows_dir.mkdir(parents=True, exist_ok=True)
    if example_path.exists():
        typer.echo(f"Note: {example_path} already exists; not overwriting.")
    else:
        example_path.write_text(example_content)

    typer.echo(_next_steps_message())
```

Detection:
```python
def _detect_project_type(root: Path) -> Literal["node", "python", "rust", "generic"]:
    if (root / "package.json").exists():
        return "node"
    if (root / "pyproject.toml").exists():
        return "python"
    if (root / "Cargo.toml").exists():
        return "rust"
    return "generic"
```

Templates loaded from `importlib.resources.files("flowstate.init_templates")`:
- `flowstate.toml.tmpl`
- `example_node.flow`
- `example_python.flow`
- `example_rust.flow`
- `example_generic.flow`

Each example flow should be a **valid** flow that `flowstate check` passes. It does not need to actually succeed at runtime (a user probably doesn't have `claude` in their path out of the box), it just needs to parse and typecheck.

### Edge Cases
- `flowstate init` run inside a nested directory of an existing Flowstate project → still create a new `flowstate.toml` at CWD (nested projects are allowed per spec). Print a note if a parent anchor is detected.
- User's CWD is not writable → bubble up the `PermissionError` with a clear message.
- `--force` overwrites `flowstate.toml` but must **not** overwrite `flows/example.flow` — that's user content.

## Testing Strategy
- Unit tests in `tests/server/test_cli_init.py` using `tmp_path` and `typer.testing.CliRunner`:
  - Fresh directory → creates all three artifacts.
  - Pre-existing `flowstate.toml` → fails without `--force`, succeeds with `--force`.
  - Pre-existing `flows/example.flow` → preserved, note printed.
  - `package.json` present → example content contains a Node-flavored marker (e.g., a comment mentioning `npm`).
  - `pyproject.toml` present → example contains Python marker.
  - `Cargo.toml` present → Rust marker.
  - None → generic marker.
- For each rendered example, run `flowstate check` on it programmatically and assert success.

## E2E Verification Plan

### Verification Steps
1. `cd /tmp && rm -rf fs-init && mkdir fs-init && cd fs-init && echo '{}' > package.json`
2. `flowstate init`
3. Assert `flowstate.toml` and `flows/example.flow` exist; assert the example flow mentions Node/npm (a comment is fine).
4. `flowstate check flows/example.flow` → PASS.
5. `flowstate init` again → fails; `flowstate init --force` → succeeds, `flowstate.toml` is regenerated, `flows/example.flow` is untouched (mtime unchanged).
6. In a directory with no recognizable manifest: generic template is used.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `init` command implemented
- [ ] Four `.flow` templates + `flowstate.toml.tmpl` bundled in the wheel
- [ ] Project-type detection works
- [ ] `--force` semantics correct
- [ ] Unit tests passing
- [ ] `/lint` passes
- [ ] E2E steps above verified
