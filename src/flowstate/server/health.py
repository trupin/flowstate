"""Unauthenticated ``GET /health`` endpoint for readiness probes.

SERVER-031 — the endpoint is deliberately minimal: it reports that the
server is alive, which version of the Flowstate package is running, and
which project the server was pinned to at startup (from ``app.state``,
not the filesystem — the running server serves exactly one project per
spec §13.3). It intentionally does **not** expose internal paths such
as ``db_path`` or ``workspaces_dir``, and it does **not** require any
authentication so external orchestrators (and the evaluator's E2E
harness) can poll it without handshaking.

The endpoint is registered on the bare FastAPI app **outside** the
``/api`` router prefix so a single ``curl http://host:port/health``
works without users having to know the internal route layout.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

if TYPE_CHECKING:
    from flowstate.config import Project

router = APIRouter()

# Sentinel version for source checkouts that haven't been ``pip install -e``'d.
# Chosen so it sorts **below** every real PEP 440 release and is visibly a
# development build to humans.
_DEV_VERSION = "0.0.0+dev"


def _resolve_version() -> str:
    """Return the Flowstate package version, or ``"0.0.0+dev"`` as a fallback.

    ``importlib.metadata.version`` raises :class:`PackageNotFoundError`
    when the package is not installed (e.g. a source checkout that has
    never been ``pip install -e .``'d into the active venv, which is
    the default state for this worktree). The endpoint must not crash
    in that case — orchestrators would flag the server as unhealthy and
    the entire deployability story collapses.
    """
    try:
        return pkg_version("flowstate")
    except PackageNotFoundError:
        return _DEV_VERSION


@router.get("/health", include_in_schema=True)
def health(request: Request) -> dict[str, Any]:
    """Return a minimal JSON readiness payload.

    The handler reads ``request.app.state.project`` — the
    :class:`flowstate.config.Project` that :func:`create_app` mounts
    during Phase 31.1 wiring. It must **not** call
    :func:`flowstate.config.resolve_project` at request time: the
    running server is pinned to a single project for its entire
    lifetime and re-walking the filesystem on every health check would
    both race against ``os.chdir`` and defeat the point of "pinned
    project".
    """
    project: Project = request.app.state.project
    return {
        "status": "ok",
        "version": _resolve_version(),
        "project": {
            "slug": project.slug,
            "root": str(project.root),
        },
    }
