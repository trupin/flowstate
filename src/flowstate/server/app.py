"""FastAPI application factory with lifespan management and error handling."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from flowstate.config import FlowstateConfig, load_config


class FlowstateError(Exception):
    """Base exception for all Flowstate server errors."""

    def __init__(
        self,
        message: str,
        details: list[str] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []
        self.status_code = status_code


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Async lifespan context manager for startup/shutdown hooks.

    Startup and shutdown logic for DB connections, file watcher, WebSocket hub,
    etc. will be added by later issues (SERVER-002, SERVER-003, SERVER-005).
    """
    yield


def create_app(
    config: FlowstateConfig | None = None,
    subprocess_manager: Any = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Optional configuration. If None, loads from TOML or defaults.
        subprocess_manager: Optional subprocess manager for test mock injection.
            Stored on app.state and passed to FlowExecutor during run creation.

    Returns:
        A configured FastAPI instance.
    """
    if config is None:
        config = load_config()

    app = FastAPI(
        title="Flowstate",
        description="State-machine orchestration for AI agents",
        lifespan=lifespan,
    )

    # Store config and optional mock subprocess manager on app state
    app.state.config = config
    app.state.subprocess_manager = subprocess_manager

    # CORS for localhost dev (React dev server)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost:\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Custom exception handler for FlowstateError
    @app.exception_handler(FlowstateError)
    async def flowstate_error_handler(request: Request, exc: FlowstateError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "details": exc.details},
        )

    return app
