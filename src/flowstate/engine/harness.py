"""Harness protocol -- structural typing for task execution backends.

Defines the ``Harness`` Protocol that formalizes the 4-method interface
shared by ``SubprocessManager``, ``SDKRunner``, ``AcpHarness``, and test
doubles.  ``HarnessManager`` is a registry that resolves harness names
(e.g. "claude") to concrete instances, lazily instantiating ``AcpHarness``
from ``HarnessConfig`` when a name is not already in the registry.

No existing class needs to inherit from ``Harness`` -- it uses structural
subtyping (duck typing checked by pyright at type-check time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from flowstate.engine.subprocess_mgr import JudgeResult, StreamEvent


class Harness(Protocol):
    """Structural protocol for task execution backends.

    Any object with these four async methods satisfies the protocol.
    ``SubprocessManager``, ``SDKRunner``, and ``MockSubprocessManager`` all
    match this shape without inheriting from ``Harness``.

    Note: ``run_task`` and ``run_task_resume`` are declared without ``async``
    because the implementations are async generators (they ``yield``), whose
    type is ``AsyncGenerator[StreamEvent, None]`` directly — not
    ``Coroutine[..., AsyncGenerator[...]]``.
    """

    def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]: ...

    def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]: ...

    async def run_judge(
        self, prompt: str, workspace: str, *, skip_permissions: bool = False
    ) -> JudgeResult: ...

    async def kill(self, session_id: str) -> None: ...


class HarnessNotFoundError(Exception):
    """Raised when a harness name cannot be resolved."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Harness '{name}' not found in registry")
        self.name = name


@dataclass
class HarnessConfig:
    """Configuration for a harness backend.

    When ``HarnessManager.get()`` encounters a name that is not yet in the
    registry but has a matching ``HarnessConfig``, it lazily creates an
    ``AcpHarness`` from the config's *command* and *env* fields.
    """

    command: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


class HarnessManager:
    """Registry that maps harness names to ``Harness`` instances.

    The *default_harness* is always registered under the name ``"claude"``.
    Additional harnesses can be registered via the *configs* parameter
    or by calling :meth:`register` directly.

    When ``get()`` is called for a name not in the registry but present in
    *configs*, an ``AcpHarness`` is lazily instantiated, registered, and
    returned.
    """

    def __init__(
        self,
        default_harness: Harness,
        configs: dict[str, HarnessConfig] | None = None,
    ) -> None:
        self._registry: dict[str, Harness] = {"claude": default_harness}
        self._configs = configs or {}

    def get(self, name: str) -> Harness:
        """Return the harness registered under *name*.

        If *name* is not in the registry but has a matching ``HarnessConfig``,
        lazily create an ``AcpHarness`` from the config, register it, and
        return it.

        Raises ``HarnessNotFoundError`` if *name* is neither registered nor
        configured.
        """
        if name in self._registry:
            return self._registry[name]

        # Lazy instantiation from config
        if name in self._configs:
            cfg = self._configs[name]
            from flowstate.engine.acp_client import AcpHarness

            harness: Harness = AcpHarness(command=cfg.command, env=cfg.env)
            self._registry[name] = harness
            return harness

        raise HarnessNotFoundError(name)

    def register(self, name: str, harness: Harness) -> None:
        """Register a harness under *name*, overwriting any existing entry."""
        self._registry[name] = harness

    @property
    def names(self) -> list[str]:
        """Return all registered harness names."""
        return list(self._registry)
