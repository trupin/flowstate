"""Sandbox manager -- wraps harness commands to run inside a named persistent sandbox.

The user creates a sandbox once (e.g. ``openshell sandbox create --name flowstate-claude ...``),
runs ``claude login`` inside it, and configures the sandbox name in ``flowstate.toml``.
Flowstate reuses this sandbox for all tasks by piping commands through
``ssh -T`` via a wrapper script (connect-wrapper.sh).
"""

import asyncio
import json
import logging
import shlex
import tempfile
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

        Uses the connect-wrapper.sh script which runs the command via
        ``ssh -T`` through ``openshell ssh-proxy`` for clean stdio.
        """
        agent_cmd = " ".join(shlex.quote(c) for c in command)
        return [self._connect_wrapper_path(), self.sandbox_name, agent_cmd]

    async def apply_network_policy(self, server_port: int) -> bool:
        """Apply a network policy allowing the sandbox to reach the host API.

        Creates a temporary policy YAML allowing egress to
        ``host.docker.internal:<server_port>`` and applies it via
        ``openshell policy set``.

        Returns True on success, False on failure.
        """
        policy = {
            "version": 1,
            "network_policies": {
                "flowstate_api": {
                    "endpoints": [
                        {"host": "host.docker.internal", "port": server_port},
                    ],
                    "binaries": [{"path": "**"}],
                },
            },
        }

        policy_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", prefix="flowstate-policy-", delete=False
            ) as f:
                # Write as YAML (json is valid yaml)
                json.dump(policy, f, indent=2)
                policy_path = f.name

            proc = await asyncio.create_subprocess_exec(
                "openshell",
                "policy",
                "set",
                self.sandbox_name,
                "--policy",
                policy_path,
                "--wait",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                logger.warning(
                    "Failed to apply sandbox network policy: %s",
                    stderr.decode().strip(),
                )
                return False

            logger.info(
                "Applied sandbox network policy: host.docker.internal:%d",
                server_port,
            )
            return True
        except (OSError, TimeoutError):
            logger.warning(
                "Failed to apply sandbox network policy",
                exc_info=True,
            )
            return False
        finally:
            if policy_path:
                Path(policy_path).unlink(missing_ok=True)
