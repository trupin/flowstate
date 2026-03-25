"""Flowstate CLI — parse, validate, run flows, and manage the server."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="flowstate",
    help="State-machine orchestration system for AI agents.",
    no_args_is_help=True,
)


@app.command()
def check(
    path: Annotated[str, typer.Argument(help="Path to a .flow file")],
) -> None:
    """Parse and type-check a .flow file."""
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
    config: Annotated[str | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Start the Flowstate web server."""
    import logging

    import uvicorn

    from flowstate.config import load_config
    from flowstate.server.app import create_app

    cfg = load_config(path=config)

    # Configure Python logging so flowstate.* loggers produce visible output
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # CLI flags override config file values
    final_host = host or cfg.server_host
    final_port = port or cfg.server_port

    application = create_app(config=cfg, static_dir=True)

    typer.echo(f"Starting Flowstate server on {final_host}:{final_port}")
    uvicorn.run(application, host=final_host, port=final_port)


@app.command()
def run(
    path: Annotated[str, typer.Argument(help="Path to a .flow file")],
    param: Annotated[
        list[str] | None, typer.Option("--param", help="Parameter as key=value")
    ] = None,
    config: Annotated[str | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Start a flow run from a .flow file."""
    file_path = Path(path)
    if not file_path.exists():
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

    # Start the run
    import asyncio
    import json

    from flowstate.config import load_config
    from flowstate.state.repository import FlowstateDB

    cfg = load_config(path=config)

    async def _run() -> str:
        db = FlowstateDB(cfg.database_path)
        try:
            # Store the flow definition so we have a reference
            flow_def_id = db.create_flow_definition(
                name=flow_ast.name,
                source_dsl=source,
                ast_json=json.dumps({"name": flow_ast.name}),
            )
            data_dir = str(Path.home() / ".flowstate" / "runs")
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


@app.command()
def runs(
    status: Annotated[str | None, typer.Option(help="Filter by status")] = None,
    config: Annotated[str | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """List all flow runs."""
    from flowstate.config import load_config
    from flowstate.state.repository import FlowstateDB

    cfg = load_config(path=config)
    db = FlowstateDB(cfg.database_path)

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
    config: Annotated[str | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Show detailed status of a flow run."""
    from flowstate.config import load_config
    from flowstate.state.repository import FlowstateDB

    cfg = load_config(path=config)
    db = FlowstateDB(cfg.database_path)

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
def schedules(
    config: Annotated[str | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """List all flow schedules."""
    from flowstate.config import load_config
    from flowstate.state.repository import FlowstateDB

    cfg = load_config(path=config)
    db = FlowstateDB(cfg.database_path)

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
    config: Annotated[str | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Manually trigger a scheduled flow."""
    from flowstate.config import load_config
    from flowstate.state.repository import FlowstateDB

    cfg = load_config(path=config)
    db = FlowstateDB(cfg.database_path)

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
        data_dir = str(Path.home() / ".flowstate" / "runs")
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
