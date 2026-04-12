"""Flowstate CLI — parse, validate, run flows, and manage the server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import typer

if TYPE_CHECKING:
    from flowstate.config import Project

app = typer.Typer(
    name="flowstate",
    help="State-machine orchestration system for AI agents.",
    no_args_is_help=True,
)

# SERVER-030: hosts that are considered safe (no "no auth" warning).
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

# Sentinel version for source checkouts that haven't been ``pip install -e``'d.
# Kept in lockstep with ``flowstate.server.health._DEV_VERSION`` — both paths
# must return the same fallback string so ``flowstate --version`` and
# ``GET /health`` agree on what "dev build" looks like to users.
_DEV_VERSION = "0.0.0+dev"


def _resolve_version() -> str:
    """Return the installed ``flowstate`` package version, or the dev fallback.

    ``importlib.metadata.version`` raises :class:`PackageNotFoundError`
    when the package is not installed into the active environment (the
    typical state for a source checkout that hasn't been ``pip install
    -e``'d). In that case we return ``"0.0.0+dev"`` — the same sentinel
    the ``/health`` endpoint uses — so humans see a clearly-a-dev-build
    string instead of a crash.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("flowstate")
    except PackageNotFoundError:
        return _DEV_VERSION


def _version_callback(value: bool) -> None:
    """Typer eager callback for ``--version`` / ``-V``.

    Runs *before* Typer dispatches to a subcommand, so ``flowstate
    --version`` succeeds outside any project (no ``_require_project``
    walk), prints a version string to stdout, and exits 0.
    """
    if not value:
        return
    typer.echo(f"flowstate {_resolve_version()}")
    raise typer.Exit(code=0)


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the Flowstate version and exit.",
            is_eager=True,
            callback=_version_callback,
        ),
    ] = False,
) -> None:
    """Flowstate top-level entry point.

    This callback exists solely to register the global ``--version`` /
    ``-V`` flag. The flag is marked ``is_eager=True`` so its callback
    fires before Typer resolves (and validates) any subcommand, which
    is what lets ``flowstate --version`` run from any directory —
    including one with no ``flowstate.toml`` anchor.
    """


def _require_project() -> Project:
    """Resolve the current project or exit with a clear, friendly error.

    Every CLI command that needs a mounted project (DB, flows_dir,
    workspaces) calls this helper at the top. The only commands that
    must **not** call it are the ones that can legitimately run outside
    any project: ``init`` (which *creates* the project), ``--version``,
    and ``--help``.

    On failure this function writes a single human-readable message to
    stderr (no traceback) and exits with code **2** — the conventional
    "usage / context error" code, distinct from code 1 used for generic
    runtime failures. The two failure modes are:

    - ``ProjectNotFoundError`` raised by :func:`flowstate.config.resolve_project`,
      either because no ``flowstate.toml`` was found walking up from CWD
      or because ``FLOWSTATE_CONFIG`` points at a missing file. The
      message discriminates between the two so a user who set the env
      var by mistake is not told to run ``flowstate init``.
    - Any other exception from TOML parsing is re-raised as a
      ``ProjectNotFoundError``-shaped message as well, still with exit
      code 2 and no traceback.
    """
    from flowstate.config import ProjectNotFoundError, resolve_project

    try:
        return resolve_project()
    except ProjectNotFoundError as exc:
        override = os.environ.get("FLOWSTATE_CONFIG")
        if override:
            # The env-var-specific failure is already phrased clearly by
            # ``_find_anchor``; forward it verbatim so the user sees the
            # exact path they set.
            typer.echo(str(exc), err=True)
        else:
            cwd = Path.cwd().resolve()
            typer.echo(
                f"No flowstate.toml found in {cwd} or any parent directory.\n"
                f"Run `flowstate init` to create one, or cd into a "
                f"Flowstate project.",
                err=True,
            )
        raise typer.Exit(code=2) from None
    except Exception as exc:
        # TOML parse errors and any other unexpected config-load failures
        # should still exit 2 with a clean message (no traceback).
        typer.echo(f"Failed to load flowstate.toml: {exc}", err=True)
        raise typer.Exit(code=2) from None


# Backward-compatible alias: several internal callers (and older tests in
# sibling worktrees) still import the Phase 31.1 helper name. Keep the two
# symbols in lockstep so there is exactly one implementation.
_resolve_project_or_exit = _require_project


def _warn_if_non_loopback(host: str, port: int) -> None:
    """Emit a loud, multi-line banner to stderr when ``host`` is non-loopback.

    Flowstate v0.1 has no authentication (see specs.md §13.4). Binding
    to ``0.0.0.0`` / ``::`` / a routable address means anyone who can
    reach the port can execute code via the Claude Code subprocess
    harness. The banner is prominent on purpose — a user who typed
    ``--host 0.0.0.0`` should not be able to miss it in their terminal
    scrollback.

    Loopback hosts (``127.0.0.1``, ``localhost``, ``::1``) are
    suppressed silently. Every other host triggers the warning exactly
    once; this function must be called from a single place in the CLI
    (``server``) so it never double-fires.
    """
    if host in LOOPBACK_HOSTS:
        return
    border = "=" * 60
    msg = (
        f"{border}\n"
        f"WARNING: Flowstate is binding to {host}:{port}.\n"
        f"Flowstate v0.1 has NO AUTHENTICATION. Anyone who can reach\n"
        f"this address can execute code on this machine via Flowstate's\n"
        f"subprocess harnesses.\n"
        f"Only use non-loopback binds in trusted networks.\n"
        f"{border}"
    )
    typer.echo(msg, err=True)


# SERVER-028: project-type detection literal and template map.
ProjectType = Literal["node", "python", "rust", "generic"]

_EXAMPLE_TEMPLATES: dict[ProjectType, str] = {
    "node": "example_node.flow",
    "python": "example_python.flow",
    "rust": "example_rust.flow",
    "generic": "example_generic.flow",
}


def _detect_project_type(root: Path) -> ProjectType:
    """Guess the kind of project living at ``root`` from manifest files.

    Detection is intentionally naive — it looks only at ``root`` (not
    ancestors, not descendants). Priority order matches the acceptance
    tests: Node > Python > Rust > generic. If multiple manifests are
    present, the first match wins; real-world polyglot repos can always
    edit ``flows/example.flow`` afterwards.
    """
    if (root / "package.json").exists():
        return "node"
    if (root / "pyproject.toml").exists():
        return "python"
    if (root / "Cargo.toml").exists():
        return "rust"
    return "generic"


def _load_init_template(name: str) -> str:
    """Read a packaged init template by filename.

    Uses ``importlib.resources`` so the lookup works identically from a
    source checkout (``uv run flowstate init``) and from an installed
    wheel (``uv tool install flowstate``).
    """
    from importlib.resources import files

    return (files("flowstate.init_templates") / name).read_text()


def _next_steps_message() -> str:
    """The "what do I do next?" banner printed after a successful init."""
    return (
        "Created flowstate.toml and flows/example.flow.\n"
        "Next:\n"
        "  flowstate check flows/example.flow\n"
        "  flowstate server"
    )


@app.command()
def init(
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite an existing flowstate.toml. Never overwrites flows/example.flow.",
        ),
    ] = False,
) -> None:
    """Bootstrap a Flowstate project in the current directory.

    Writes ``./flowstate.toml`` with sensible defaults and seeds
    ``./flows/example.flow`` with a starter flow picked by project-type
    detection (``package.json`` → Node, ``pyproject.toml`` → Python,
    ``Cargo.toml`` → Rust, otherwise a generic "hello flowstate" flow).

    This command is the single exception to the ``_require_project``
    rule — it bypasses the "must be inside a Flowstate project" check
    because it's the command that *creates* the project.
    """
    cwd = Path.cwd().resolve()
    toml_path = cwd / "flowstate.toml"
    flows_dir = cwd / "flows"
    example_path = flows_dir / "example.flow"

    if toml_path.exists() and not force:
        typer.echo(
            f"flowstate.toml already exists at {toml_path}. " f"Use --force to overwrite.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        project_type = _detect_project_type(cwd)
        toml_content = _load_init_template("flowstate.toml.tmpl")
        example_content = _load_init_template(_EXAMPLE_TEMPLATES[project_type])

        toml_path.write_text(toml_content)
        flows_dir.mkdir(parents=True, exist_ok=True)
        if example_path.exists():
            typer.echo(
                f"Note: {example_path} already exists; not overwriting.",
            )
        else:
            example_path.write_text(example_content)
    except PermissionError as exc:
        typer.echo(f"Permission denied while writing project files: {exc}", err=True)
        raise typer.Exit(code=2) from None
    except OSError as exc:
        typer.echo(f"Failed to write project files: {exc}", err=True)
        raise typer.Exit(code=2) from None

    typer.echo(_next_steps_message())


@app.command()
def check(
    path: Annotated[str, typer.Argument(help="Path to a .flow file")],
) -> None:
    """Parse and type-check a .flow file.

    ``check`` takes an explicit flow path and therefore does not require
    a surrounding project. It remains the one CLI command that can be
    invoked from anywhere on disk.
    """
    file_path = Path(path)
    if not file_path.exists():
        typer.echo(f"Error: File not found: {path}", err=True)
        raise typer.Exit(code=1)

    source = file_path.read_text()

    from flowstate.dsl.exceptions import FlowParseError
    from flowstate.dsl.parser import parse_flow
    from flowstate.dsl.type_checker import check_flow

    try:
        flow_ast = parse_flow(source)
    except FlowParseError as e:
        typer.echo(f"Parse error: {e}", err=True)
        raise typer.Exit(code=1) from None

    errors = check_flow(flow_ast)
    if errors:
        for error in errors:
            typer.echo(f"Type error: {error}", err=True)
        raise typer.Exit(code=1)

    typer.echo("OK")


@app.command()
def server(
    host: Annotated[str | None, typer.Option(help="Server host")] = None,
    port: Annotated[int | None, typer.Option(help="Server port")] = None,
) -> None:
    """Start the Flowstate web server bound to the current project."""
    import logging

    import uvicorn

    from flowstate.server.app import create_app

    project = _require_project()
    cfg = project.config

    # Configure Python logging so flowstate.* loggers produce visible output
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Host/port precedence (high → low): CLI flag > flowstate.toml > dataclass default.
    # `cfg.server_host`/`cfg.server_port` already carry either the TOML
    # value or the `FlowstateConfig` default (``127.0.0.1``/``9090``), so
    # we only overwrite them when the user passed an explicit CLI flag.
    if host:
        cfg.server_host = host
    if port:
        cfg.server_port = port

    # SERVER-030: loud warning for non-loopback binds. Must run exactly once,
    # after host resolution and before uvicorn starts accepting connections.
    _warn_if_non_loopback(cfg.server_host, cfg.server_port)

    application = create_app(project=project, static_dir=True)

    typer.echo(f"Starting Flowstate server on {cfg.server_host}:{cfg.server_port}")
    typer.echo(f"Project: {project.root} (slug={project.slug})")
    uvicorn.run(application, host=cfg.server_host, port=cfg.server_port)


@app.command()
def run(
    path: Annotated[str, typer.Argument(help="Path to a .flow file")],
    param: Annotated[
        list[str] | None, typer.Option("--param", help="Parameter as key=value")
    ] = None,
    server: Annotated[
        str | None, typer.Option("--server", help="Flowstate server URL for artifact API")
    ] = None,
) -> None:
    """Start a flow run from a .flow file."""
    project = _require_project()
    cfg = project.config

    flow_path = Path(path)
    if not flow_path.is_absolute():
        flow_path = (project.root / flow_path).resolve()
    if not flow_path.exists():
        typer.echo(f"Error: File not found: {path}", err=True)
        raise typer.Exit(code=1)

    # Parse params from key=value pairs
    params: dict[str, str] = {}
    for p in param or []:
        if "=" not in p:
            typer.echo(f"Error: Invalid param format '{p}'. Use key=value.", err=True)
            raise typer.Exit(code=1)
        key, _, value = p.partition("=")
        params[key] = value

    source = flow_path.read_text()

    from flowstate.dsl.exceptions import FlowParseError
    from flowstate.dsl.parser import parse_flow
    from flowstate.dsl.type_checker import check_flow

    try:
        flow_ast = parse_flow(source)
    except FlowParseError as e:
        typer.echo(f"Parse error: {e}", err=True)
        raise typer.Exit(code=1) from None

    errors = check_flow(flow_ast)
    if errors:
        for error in errors:
            typer.echo(f"Type error: {error}", err=True)
        raise typer.Exit(code=1)

    # Start the run
    import asyncio
    import json

    from flowstate.state.repository import FlowstateDB

    # Resolve server URL: CLI flag takes precedence over config
    server_base_url = server or f"http://{cfg.server_host}:{cfg.server_port}"

    async def _run() -> str:
        db = FlowstateDB(project.db_path)
        try:
            # Store the flow definition so we have a reference
            flow_def_id = db.create_flow_definition(
                name=flow_ast.name,
                source_dsl=source,
                ast_json=json.dumps({"name": flow_ast.name}),
            )
            # Runs data dir lives under the project's data_dir (SERVER-026).
            data_dir = str(project.data_dir / "runs")
            run_id = db.create_flow_run(
                flow_definition_id=flow_def_id,
                data_dir=data_dir,
                budget_seconds=flow_ast.budget_seconds,
                on_error=flow_ast.on_error.value,
                default_workspace=flow_ast.workspace,
                params_json=json.dumps(params) if params else None,
            )
            return run_id
        finally:
            db.close()

    run_id = asyncio.run(_run())
    typer.echo(f"Run started: {run_id}")
    typer.echo(f"Server URL: {server_base_url}")


@app.command()
def runs(
    status: Annotated[str | None, typer.Option(help="Filter by status")] = None,
) -> None:
    """List all flow runs."""
    from flowstate.state.repository import FlowstateDB

    project = _require_project()
    db = FlowstateDB(project.db_path)

    try:
        all_runs = db.list_flow_runs(status=status)

        if not all_runs:
            typer.echo("No runs found.")
            return

        # Build a lookup from flow_definition_id -> name
        flow_defs = db.list_flow_definitions()
        flow_name_map = {fd.id: fd.name for fd in flow_defs}

        # Table header
        typer.echo(f"{'ID':<12} {'Flow':<20} {'Status':<12} {'Started':<20}")
        typer.echo("-" * 64)
        for r in all_runs:
            short_id = r.id[:8] + "..."
            flow_name = flow_name_map.get(r.flow_definition_id, "unknown")
            started = r.started_at or r.created_at
            typer.echo(f"{short_id:<12} {flow_name:<20} {r.status:<12} {started}")
    finally:
        db.close()


@app.command()
def status(
    run_id: Annotated[str, typer.Argument(help="Run ID (or prefix)")],
) -> None:
    """Show detailed status of a flow run."""
    from flowstate.state.repository import FlowstateDB

    project = _require_project()
    db = FlowstateDB(project.db_path)

    try:
        # Try exact match first
        matched_run = db.get_flow_run(run_id)
        if not matched_run:
            # Try prefix match
            all_runs = db.list_flow_runs()
            matches = [r for r in all_runs if r.id.startswith(run_id)]
            if len(matches) == 0:
                typer.echo(f"Error: Run '{run_id}' not found.", err=True)
                raise typer.Exit(code=1)
            elif len(matches) > 1:
                typer.echo(f"Error: Ambiguous run ID prefix '{run_id}'. Matches:", err=True)
                for m in matches:
                    typer.echo(f"  {m.id}", err=True)
                raise typer.Exit(code=1)
            matched_run = matches[0]

        # Look up flow name
        flow_def = db.get_flow_definition(matched_run.flow_definition_id)
        flow_name = flow_def.name if flow_def else "unknown"

        typer.echo(f"Run: {matched_run.id}")
        typer.echo(f"Flow: {flow_name}")
        typer.echo(f"Status: {matched_run.status}")
        typer.echo(f"Elapsed: {matched_run.elapsed_seconds:.1f}s / {matched_run.budget_seconds}s")
        typer.echo()

        tasks = db.list_task_executions(matched_run.id)
        if tasks:
            typer.echo("Tasks:")
            for t in tasks:
                typer.echo(f"  {t.node_name} (gen {t.generation}): {t.status}")
    finally:
        db.close()


@app.command()
def schedules() -> None:
    """List all flow schedules."""
    from flowstate.state.repository import FlowstateDB

    project = _require_project()
    db = FlowstateDB(project.db_path)

    try:
        all_schedules = db.list_flow_schedules()

        if not all_schedules:
            typer.echo("No schedules found.")
            return

        # Build a lookup from flow_definition_id -> name
        flow_defs = db.list_flow_definitions()
        flow_name_map = {fd.id: fd.name for fd in flow_defs}

        typer.echo(f"{'Flow':<20} {'Cron':<20} {'Status':<10} {'Next Run':<20}")
        typer.echo("-" * 70)
        for s in all_schedules:
            flow_name = flow_name_map.get(s.flow_definition_id, "unknown")
            sched_status = "enabled" if s.enabled else "disabled"
            next_run = s.next_trigger_at or "\u2014"
            typer.echo(f"{flow_name:<20} {s.cron_expression:<20} {sched_status:<10} {next_run}")
    finally:
        db.close()


@app.command()
def trigger(
    flow_name: Annotated[str, typer.Argument(help="Flow name to trigger")],
) -> None:
    """Manually trigger a scheduled flow."""
    from flowstate.state.repository import FlowstateDB

    project = _require_project()
    db = FlowstateDB(project.db_path)

    try:
        # Find the flow definition by name
        flow_def = db.get_flow_definition_by_name(flow_name)
        if not flow_def:
            typer.echo(f"Error: No flow found with name '{flow_name}'.", err=True)
            raise typer.Exit(code=1)

        # Check for a schedule
        all_schedules = db.list_flow_schedules(flow_definition_id=flow_def.id)
        if not all_schedules:
            typer.echo(f"Error: No schedule found for flow '{flow_name}'.", err=True)
            raise typer.Exit(code=1)

        # Create a run for the scheduled flow
        data_dir = str(project.data_dir / "runs")
        run_id = db.create_flow_run(
            flow_definition_id=flow_def.id,
            data_dir=data_dir,
            budget_seconds=3600,  # default; would parse from AST in full implementation
            on_error="pause",
        )
        typer.echo(f"Triggered: {run_id}")
    finally:
        db.close()


if __name__ == "__main__":
    app()
