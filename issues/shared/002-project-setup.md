# [SHARED-002] Project Setup (pyproject.toml, directory structure)

## Domain
shared

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: none
- Blocks: SERVER-001

## Spec References
- specs.md Section 13 — "Configuration"
- CLAUDE.md — "Build & Dev Commands" and "Architecture & Module Boundaries"

## Summary
Bootstrap the Flowstate Python project: create `pyproject.toml` with all dependencies managed by `uv`, define the CLI entry point, establish the full package directory structure with empty `__init__.py` files, and configure all development tools (ruff, pyright, pytest). After this issue is done, `uv sync` installs everything, `uv run pytest` runs (with 0 tests collected), `uv run ruff check .` passes, and `uv run pyright` passes.

## Acceptance Criteria
- [ ] `pyproject.toml` exists at the project root with:
  - `[project]` section: name = "flowstate", version = "0.1.0", requires-python = ">=3.12"
  - Runtime dependencies: `lark`, `pydantic`, `fastapi`, `uvicorn[standard]`, `typer`, `tomli`, `watchfiles`
  - Dev dependencies (in `[dependency-groups]`): `pytest`, `pytest-asyncio`, `ruff`, `pyright`, `httpx` (for FastAPI TestClient)
  - Entry point: `[project.scripts]` section with `flowstate = "flowstate.cli:app"`
  - Build backend: `hatchling` with `packages = ["src/flowstate"]`
- [ ] Directory structure exists:
  ```
  src/flowstate/__init__.py
  src/flowstate/dsl/__init__.py
  src/flowstate/state/__init__.py
  src/flowstate/engine/__init__.py
  src/flowstate/server/__init__.py
  src/flowstate/cli.py               (minimal placeholder)
  tests/__init__.py
  tests/dsl/__init__.py
  tests/state/__init__.py
  tests/engine/__init__.py
  tests/server/__init__.py
  tests/dsl/fixtures/                 (empty directory with .gitkeep)
  ```
- [ ] `ruff` configured: line-length = 100, target Python 3.12, `src = ["src"]`
- [ ] `pyright` configured: standard mode, `pythonVersion = "3.12"`, `venvPath = "."`, `venv = ".venv"`
- [ ] `pytest` configured: testpaths = `["tests"]`, asyncio_mode = "auto"
- [ ] `uv sync` succeeds with no errors
- [ ] `uv run pytest` runs and exits 0 (0 tests collected is fine)
- [ ] `uv run ruff check .` exits 0
- [ ] `uv run ruff format --check .` exits 0
- [ ] `uv run pyright` exits 0
- [ ] The CLI placeholder allows `uv run flowstate --help` to print a help message without crashing

## Technical Design

### Files to Create/Modify
- `pyproject.toml` — project metadata, dependencies, tool configuration
- `src/flowstate/__init__.py` — package init (can contain `__version__ = "0.1.0"`)
- `src/flowstate/dsl/__init__.py` — empty
- `src/flowstate/state/__init__.py` — empty
- `src/flowstate/engine/__init__.py` — empty
- `src/flowstate/server/__init__.py` — empty
- `src/flowstate/cli.py` — minimal typer app placeholder
- `tests/__init__.py` — empty
- `tests/dsl/__init__.py` — empty
- `tests/dsl/fixtures/.gitkeep` — keep empty fixtures directory in git
- `tests/state/__init__.py` — empty
- `tests/engine/__init__.py` — empty
- `tests/server/__init__.py` — empty

### Key Implementation Details

#### pyproject.toml

```toml
[project]
name = "flowstate"
version = "0.1.0"
description = "State-machine orchestration system for AI agents"
requires-python = ">=3.12"
dependencies = [
    "lark>=1.1",
    "pydantic>=2.0",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "typer>=0.9",
    "tomli>=2.0",
    "watchfiles>=0.21",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.3",
    "pyright>=1.1",
    "httpx>=0.27",
]

[project.scripts]
flowstate = "flowstate.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/flowstate"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pyright]
pythonVersion = "3.12"
typeCheckingMode = "standard"
venvPath = "."
venv = ".venv"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

#### CLI Placeholder (`src/flowstate/cli.py`)

A minimal typer application that proves the entry point works:

```python
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
```

This provides `uv run flowstate --help`, `uv run flowstate check <path>`, and `uv run flowstate server` as placeholders. Future issues (SERVER-007) will flesh out the full CLI.

#### Package `__init__.py` files

All `__init__.py` files should be empty except `src/flowstate/__init__.py` which may optionally contain:

```python
__version__ = "0.1.0"
```

#### Directory for test fixtures

Create `tests/dsl/fixtures/.gitkeep` (an empty file) so git tracks the empty directory. The DSL agent will populate this with `.flow` fixture files.

### Edge Cases
- `tomli` is only needed for Python < 3.11 (stdlib `tomllib` exists in 3.11+), but including it as a dependency keeps the code simpler — the codebase can `import tomli` uniformly, or use a compatibility shim. Since the project targets 3.12+, the server code can use `tomllib` from stdlib and drop `tomli` later. Include it now per the user's requirement.
- `uvicorn[standard]` includes `uvloop` and `httptools` for better performance. The `[standard]` extra is important.
- The `hatchling` build backend with `packages = ["src/flowstate"]` ensures the `src/` layout works correctly — without this, pip/uv won't find the package.
- `pyright` needs `venvPath` and `venv` settings to find the dependencies installed by `uv` in `.venv/`.
- The `[tool.ruff.lint] select` list enables: E (pycodestyle errors), F (pyflakes), I (isort), UP (pyupgrade), B (bugbear), SIM (simplify). This is a good baseline without being overly strict.

## Testing Strategy

This issue has no unit tests — it is verified by running the toolchain:

1. `uv sync` — must exit 0, producing a `.venv/` with all dependencies installed.
2. `uv run pytest` — must exit 0 (0 tests collected is acceptable).
3. `uv run ruff check .` — must exit 0 with no lint errors.
4. `uv run ruff format --check .` — must exit 0 (all files already formatted).
5. `uv run pyright` — must exit 0 with no type errors.
6. `uv run flowstate --help` — must print the help message and exit 0.
7. Verify directory structure: all `__init__.py` files exist, `tests/dsl/fixtures/` directory exists.
