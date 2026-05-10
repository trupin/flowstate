"""Allow ``python -m flowstate`` as an alias for the ``flowstate`` console script.

The Tauri menubar app (UI-074) spawns the server as a child process with
``python -m flowstate server``. Without this module, that invocation fails
with ``'flowstate' is a package and cannot be directly executed`` because
the entry point lives only in the ``flowstate.cli:app`` Typer app reachable
via the installed ``flowstate`` console script.
"""

from flowstate.cli import app

if __name__ == "__main__":
    app()
