import typer

app = typer.Typer(
    name="flowstate",
    help="State-machine orchestration system for AI agents.",
    no_args_is_help=True,
)


@app.command()
def check(path: str) -> None:
    """Parse and type-check a .flow file."""
    typer.echo(f"Checking {path}... (not yet implemented)")


@app.command()
def server() -> None:
    """Start the Flowstate web server."""
    typer.echo("Starting server... (not yet implemented)")


if __name__ == "__main__":
    app()
