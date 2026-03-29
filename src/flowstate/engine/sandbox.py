"""Sandbox manager -- wraps harness commands to run inside a named persistent sandbox.

The user creates a sandbox once (e.g. ``openshell sandbox create --name flowstate-claude ...``),
runs ``claude login`` inside it, and configures the sandbox name in ``flowstate.toml``.
Flowstate reuses this sandbox for all tasks by piping commands through
``openshell sandbox connect`` via a wrapper script.
"""

import asyncio
import logging
import shlex
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SandboxManager:
    """Wraps harness commands to run inside a named persistent OpenShell sandbox.

    The sandbox must already exist and be in Ready state.  No per-task
    creation or destruction is performed -- the user manages the sandbox
    lifecycle externally.
    """

    sandbox_name: str = "flowstate-claude"

    def _connect_wrapper_path(self) -> str:
        """Return the absolute path to the connect-wrapper.sh script."""
        return str(Path(__file__).parent / "sandbox" / "connect-wrapper.sh")

    def wrap_command(self, command: list[str]) -> list[str]:
        """Wrap a command to run inside the named persistent sandbox.

        Uses the connect-wrapper.sh script to pipe the command through
        ``openshell sandbox connect``.
        """
        agent_cmd = " ".join(shlex.quote(c) for c in command)
        return [self._connect_wrapper_path(), self.sandbox_name, agent_cmd]

    async def download_file(self, sandbox_path: str, host_path: str) -> bool:
        """Download a file from the sandbox to the host.

        Uses ``openshell sandbox download`` to copy a file from the sandbox
        filesystem to a local path.  Returns True on success, False on failure
        (e.g. the file does not exist inside the sandbox).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "openshell",
                "sandbox",
                "download",
                self.sandbox_name,
                sandbox_path,
                host_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except OSError:
            logger.debug(
                "Failed to download %s from sandbox %s: openshell not available",
                sandbox_path,
                self.sandbox_name,
            )
            return False
