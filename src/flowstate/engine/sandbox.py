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

        Pipes ``cat <file>`` through ``openshell sandbox connect`` to read the
        file content, then writes it locally.  This avoids ``openshell sandbox
        download`` which is broken by Landlock warnings corrupting the tar stream.

        Returns True on success, False on failure (e.g. file not found).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                self._connect_wrapper_path(),
                self.sandbox_name,
                f"cat {shlex.quote(sandbox_path)}",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode != 0 or not stdout:
                return False
            # Filter out non-content lines (shell control codes, warnings)
            lines = stdout.decode("utf-8", errors="replace").splitlines()
            content_lines = [
                line
                for line in lines
                if not line.startswith("\x1b[")
                and "WARN" not in line
                and "landlock" not in line
                and "stty" not in line
                and "\x1b[?2004" not in line
            ]
            content = "\n".join(content_lines).strip()
            if not content:
                return False
            Path(host_path).parent.mkdir(parents=True, exist_ok=True)
            Path(host_path).write_text(content)
            return True
        except (OSError, TimeoutError):
            logger.debug(
                "Failed to download %s from sandbox %s",
                sandbox_path,
                self.sandbox_name,
                exc_info=True,
            )
            return False
