"""Sandbox manager — wraps harness commands with OpenShell sandbox lifecycle."""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


class SandboxError(Exception):
    """Raised when an OpenShell sandbox operation fails."""


@dataclass
class SandboxManager:
    """Manages OpenShell sandbox creation, tracking, and destruction.

    Wraps harness commands with ``openshell sandbox create`` and tracks
    active sandboxes so they can be cleaned up on task completion or
    flow abort. Destruction is best-effort — failures are logged but
    never raised, since cleanup failure should not block the flow.
    """

    _active_sandboxes: set[str] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def sandbox_name(self, task_execution_id: str) -> str:
        """Generate a deterministic sandbox name from a task execution ID.

        Returns a name of the form ``fs-<first 12 chars of ID>``.
        """
        return f"fs-{task_execution_id[:12]}"

    def _dockerfile_path(self) -> str:
        """Return the path to the sandbox Dockerfile directory."""
        return str(Path(__file__).parent / "sandbox")

    def _claude_credentials_path(self) -> str | None:
        """Return the path to ~/.claude.json if it exists."""
        creds = Path.home() / ".claude.json"
        return str(creds) if creds.exists() else None

    def wrap_command(
        self,
        command: list[str],
        task_execution_id: str,
        sandbox_policy: str | None = None,
    ) -> list[str]:
        """Wrap a harness command to run inside an OpenShell sandbox.

        Uses ``openshell sandbox create`` with the custom Dockerfile and
        ``--no-keep`` to create a sandbox, run the command, and auto-delete
        on exit. Uploads ``~/.claude.json`` so the agent can authenticate.

        The sandbox provisioning (image build + push + pull) takes ~35s on
        first run. The ACP init timeout should be set to at least 120s for
        sandboxed tasks.
        """
        name = self.sandbox_name(task_execution_id)
        wrapped = [
            "openshell",
            "sandbox",
            "create",
            "--name",
            name,
            "--from",
            self._dockerfile_path(),
            "--auto-providers",
            "--no-tty",
            "--no-keep",
        ]
        creds = self._claude_credentials_path()
        if creds:
            wrapped.extend(["--upload", f"{creds}:/sandbox/.claude.json"])
        if sandbox_policy:
            wrapped.extend(["--policy", sandbox_policy])
        wrapped.extend(["--", *command])
        return wrapped

    async def register(self, task_execution_id: str) -> None:
        """Track a sandbox as active."""
        async with self._lock:
            self._active_sandboxes.add(self.sandbox_name(task_execution_id))

    async def destroy(self, task_execution_id: str) -> None:
        """Destroy a single sandbox (best-effort)."""
        name = self.sandbox_name(task_execution_id)
        async with self._lock:
            self._active_sandboxes.discard(name)
        try:
            proc = await asyncio.create_subprocess_exec(
                "openshell",
                "sandbox",
                "delete",
                name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                logger.warning(
                    "openshell sandbox delete %s exited with code %s", name, proc.returncode
                )
        except OSError:
            logger.warning("Failed to run openshell sandbox delete %s", name, exc_info=True)

    async def destroy_all(self) -> None:
        """Destroy all tracked sandboxes (best-effort)."""
        async with self._lock:
            names = list(self._active_sandboxes)
            self._active_sandboxes.clear()
        for name in names:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "openshell",
                    "sandbox",
                    "delete",
                    name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
                if proc.returncode != 0:
                    logger.warning(
                        "openshell sandbox delete %s exited with code %s", name, proc.returncode
                    )
            except OSError:
                logger.warning("Failed to run openshell sandbox delete %s", name, exc_info=True)
