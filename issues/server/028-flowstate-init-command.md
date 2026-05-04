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
- [x] `flowstate init` creates `./flowstate.toml` with sane defaults: `host = "127.0.0.1"`, `port = 9090`, `watch_dir = "flows"`, plus commented-out harness examples.
- [x] `flowstate init` creates `./flows/` if missing.
- [x] `flowstate init` writes `./flows/example.flow` with content picked by project-type detection:
  - `package.json` present → Node-flavored `install → build → test` flow
  - `pyproject.toml` present → Python `install → lint → test` flow
  - `Cargo.toml` present → Rust `build → test` flow
  - None of the above → generic "hello flowstate" single-node flow
- [x] If `flowstate.toml` already exists, `flowstate init` refuses to run unless `--force` is passed, in which case it overwrites `flowstate.toml` only (never overwrites existing flow files).
- [x] If `flows/example.flow` already exists, it is not overwritten — the command prints a note and continues.
- [x] After success, the command prints a "next steps" message:
  ```
  Created flowstate.toml and flows/example.flow.
  Next:
    flowstate check flows/example.flow
    flowstate server
  ```
- [x] Exit code is 0 on success, non-zero on any failure (including "file exists without --force").

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

### Post-Implementation Verification (2026-04-11)

Canonical TEST-17 journey executed against the real CLI (`uv --project
<worktree> run flowstate ...`) in a fresh `/tmp` scratch directory.
Full transcript of every step and the observed output:

```
===== STEP 1: SERVER-029 outside project (expect exit 2) =====
exit_code=2
---stderr---
No flowstate.toml found in / or any parent directory.
Run `flowstate init` to create one, or cd into a Flowstate project.
---end---
STEP 1 PASS

===== STEP 2: SERVER-028 init with Node detection =====
Created flowstate.toml and flows/example.flow.
Next:
  flowstate check flows/example.flow
  flowstate server
STEP 2 PASS

===== STEP 3: SERVER-028 check passes on scaffolded flow =====
OK
STEP 3 PASS

===== STEP 4: SERVER-031 /health endpoint =====
server pid=95999
ready after 2s
{"status":"ok","version":"0.1.0","project":{"slug":"fs-phase312-proj-687706de","root":"/private/tmp/fs-phase312-proj"}}
STEP 4 PASS

===== STEP 5: SERVER-030 non-loopback warning =====
server pid=96073
---warn.log---
============================================================
WARNING: Flowstate is binding to 0.0.0.0:9098.
Flowstate v0.1 has NO AUTHENTICATION. Anyone who can reach
this address can execute code on this machine via Flowstate's
subprocess harnesses.
Only use non-loopback binds in trusted networks.
============================================================
Starting Flowstate server on 0.0.0.0:9098
Project: /private/tmp/fs-phase312-proj (slug=fs-phase312-proj-687706de)
INFO:     Uvicorn running on http://0.0.0.0:9098 (Press CTRL+C to quit)
---end---
server reachable on 9098
STEP 5 PASS

===== STEP 6: init without --force when toml exists => exit 1 =====
exit_code=1
flowstate.toml already exists at /private/tmp/fs-phase312-proj/flowstate.toml. Use --force to overwrite.
STEP 6 PASS

===== STEP 7: --force rewrites toml, preserves example.flow =====
Note: /private/tmp/fs-phase312-proj/flows/example.flow already exists; not overwriting.
Created flowstate.toml and flows/example.flow.
Next:
  flowstate check flows/example.flow
  flowstate server
stat before=1767243600 after=1767243600
STEP 7 PASS

ALL STEPS PASSED
```

Notes:
- `project.root` resolves to `/private/tmp/fs-phase312-proj` on macOS
  because `/tmp` is a symlink to `/private/tmp` and `Project.root` is
  always `.resolve()`-d. This is per the Phase 31.1 Project contract
  in `src/flowstate/config.py`, and satisfies TEST-17's "resolved
  absolute path" requirement.
- The scaffolded flow (`flows/example.flow`) was type-checked via the
  real CLI in STEP 3 — the Node template is parseable and type-safe.
- STEP 7 proves `--force` rewrote `flowstate.toml` while leaving
  `flows/example.flow` untouched (mtime byte-equal before/after).
- Initial init (STEP 2) detected Node via `package.json` and produced
  an `example.flow` containing the string `npm` (grepped inside the
  test script, not shown above).

## E2E Verification Log — Fix-loop round 1 (2026-04-11)

The Phase 31.2 evaluator flagged a pre-existing UI-dist WARNING log on
default-bind server startup as a strict-reading TEST-11 / TEST-17-step-4
failure. This was noise from before Phase 31 (`flowstate/server/app.py`
emitted `logger.warning("UI dist directory not found at ...")` whenever
the UI bundle had not been built). Because the UI is optional per
spec §13.4, the message was downgraded to `logger.info` and reworded:
`"UI bundle not found at <path>; serving API only. Run 'cd ui && npm
run build' if you want the web UI."`

Verified against the real server on a scratch project (not via
TestClient):

```
$ rm -rf /tmp/fs-fixloop-server && mkdir -p /tmp/fs-fixloop-server && cd /tmp/fs-fixloop-server
$ uv --project <worktree> run flowstate init
Created flowstate.toml and flows/example.flow.
Next:
  flowstate check flows/example.flow
  flowstate server

$ export FLOWSTATE_DATA_DIR=/tmp/fs-fixloop-data
$ nohup uv --project <worktree> run flowstate server --port 9193 > server-default.log 2>&1 &
$ curl -s http://127.0.0.1:9193/health
{"status":"ok","version":"0.1.0","project":{"slug":"fs-fixloop-server-b8c6ea27","root":"/private/tmp/fs-fixloop-server"}}

$ cat server-default.log
2026-04-11 21:26:58,244 INFO  flowstate.server.app: UI bundle not found at .../ui/dist; serving API only. Run 'cd ui && npm run build' if you want the web UI.
Starting Flowstate server on 127.0.0.1:9193
Project: /private/tmp/fs-fixloop-server (slug=fs-fixloop-server-b8c6ea27)
INFO:     Uvicorn running on http://127.0.0.1:9193 (Press CTRL+C to quit)

$ grep -c WARNING server-default.log    # 0 — no WARNING tokens
$ grep -c '=====' server-default.log    # 0 — no host banner border
```

Cross-check: the **host-warning banner** for non-loopback binds is
still emitted. Proof on the same worktree:

```
$ nohup uv --project <worktree> run flowstate server --host 0.0.0.0 --port 9194 > server-open.log 2>&1 &
$ curl -s http://127.0.0.1:9194/health   # → 200
$ head -7 server-open.log
============================================================
WARNING: Flowstate is binding to 0.0.0.0:9194.
Flowstate v0.1 has NO AUTHENTICATION. Anyone who can reach
this address can execute code on this machine via Flowstate's
subprocess harnesses.
Only use non-loopback binds in trusted networks.
============================================================
```

Tests touched: `tests/server/test_static_files.py::test_info_logged`
and `::test_info_logged_missing_index` — both updated to assert the
log level is `INFO`, not `WARNING`, and to match the new message text.
Targeted suite: `pytest tests/server/test_static_files.py` → all green.

## Completion Checklist
- [x] `init` command implemented
- [x] Four `.flow` templates + `flowstate.toml.tmpl` bundled in the wheel
- [x] Project-type detection works
- [x] `--force` semantics correct
- [x] Unit tests passing
- [x] `/lint` passes
- [x] E2E steps above verified
- [x] Fix-loop round 1: UI-bundle log downgraded to INFO
