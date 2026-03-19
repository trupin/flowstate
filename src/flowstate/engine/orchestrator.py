"""Orchestrator session manager -- tracks long-lived Claude Code sessions.

Instead of spawning a new process per task, the orchestrator session is
resumed for each action. The first call creates a fresh session with a
system prompt; subsequent calls return the existing session for resume.

Each session is keyed by (harness, cwd) so different working directories
or harness names get independent orchestrator sessions within the same
flow run.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from flowstate.engine.context import build_orchestrator_system_prompt

if TYPE_CHECKING:
    from flowstate.dsl.ast import Flow
    from flowstate.engine.subprocess_mgr import SubprocessManager


@dataclass
class OrchestratorSession:
    """Tracks a long-lived Claude Code session for an orchestrator."""

    session_id: str
    harness: str
    cwd: str
    data_dir: str
    system_prompt: str = ""
    is_initialized: bool = False


class OrchestratorManager:
    """Tracks long-lived Claude Code sessions per (harness, cwd) within a flow run.

    Instead of spawning a new process per task, the orchestrator session is
    resumed for each action. The first call creates a fresh session with a
    system prompt; subsequent calls return the existing session for resume.
    """

    def __init__(self, subprocess_mgr: SubprocessManager) -> None:
        self._subprocess_mgr = subprocess_mgr
        self._sessions: dict[str, OrchestratorSession] = {}
        self._lock = asyncio.Lock()

    def _session_key(self, harness: str, cwd: str) -> str:
        """Generate a unique key for a (harness, cwd) pair."""
        cwd_hash = hashlib.sha256(cwd.encode()).hexdigest()[:12]
        return f"{harness}-{cwd_hash}"

    async def get_or_create(
        self,
        harness: str,
        cwd: str,
        flow: Flow,
        run_id: str,
        run_data_dir: str,
        *,
        skip_permissions: bool = False,
    ) -> OrchestratorSession:
        """Get existing or create a new orchestrator session.

        First call for a (harness, cwd): creates session via subprocess_mgr.run_task()
        with orchestrator system prompt. Stores session_id to disk for recovery.

        Subsequent calls: returns cached session. The caller should use
        subprocess_mgr.run_task_resume() with the session_id.
        """
        key = self._session_key(harness, cwd)
        async with self._lock:
            if key in self._sessions:
                return self._sessions[key]
            session = await self._create_session(
                key,
                harness,
                cwd,
                flow,
                run_id,
                run_data_dir,
                skip_permissions=skip_permissions,
            )
            self._sessions[key] = session
            return session

    async def _create_session(
        self,
        key: str,
        harness: str,
        cwd: str,
        flow: Flow,
        run_id: str,
        run_data_dir: str,
        *,
        skip_permissions: bool = False,
    ) -> OrchestratorSession:
        """Create a new orchestrator session (lazy — no subprocess spawned yet).

        The session is created with metadata only. The actual Claude Code
        subprocess is spawned on the first task execution via
        run_first_task_with_system_prompt(), which combines the system prompt
        with the first task instruction in a single call. This avoids blocking
        on a separate init subprocess.
        """
        session_id = str(uuid.uuid4())
        system_prompt = build_orchestrator_system_prompt(flow, run_data_dir, cwd)

        # Create orchestrator data directory and persist session info
        orch_dir = Path(run_data_dir) / "orchestrator" / key
        orch_dir.mkdir(parents=True, exist_ok=True)

        (orch_dir / "system_prompt.md").write_text(system_prompt)
        (orch_dir / "session_id").write_text(session_id)

        return OrchestratorSession(
            session_id=session_id,
            harness=harness,
            cwd=cwd,
            data_dir=str(orch_dir),
            system_prompt=system_prompt,
            is_initialized=False,
        )

    async def terminate(self, session_id: str) -> None:
        """Terminate a specific orchestrator session."""
        await self._subprocess_mgr.kill(session_id)
        # Remove from tracking
        keys_to_remove = [k for k, s in self._sessions.items() if s.session_id == session_id]
        for key in keys_to_remove:
            del self._sessions[key]

    async def terminate_all(self, run_id: str) -> None:
        """Terminate all orchestrator sessions."""
        for session in list(self._sessions.values()):
            await self._subprocess_mgr.kill(session.session_id)
        self._sessions.clear()


def build_task_instruction(
    node_name: str,
    generation: int,
    input_path: str,
    task_dir: str,
    cwd: str,
) -> str:
    """Build the short instruction to resume the orchestrator for a task."""
    return (
        f'Execute task "{node_name}" (generation {generation}).\n'
        f"\n"
        f"Read the full task context from: {input_path}\n"
        f"Spawn a subagent to execute the task. The subagent should:\n"
        f"- Work in directory: {cwd}\n"
        f"- Write SUMMARY.md to: {task_dir}/SUMMARY.md\n"
        f"\n"
        f'Use the Agent tool with model: "opus" to spawn the subagent.'
    )


def build_judge_instruction(
    node_name: str,
    request_path: str,
    decision_path: str,
    targets: list[str],
) -> str:
    """Build the short instruction to resume the orchestrator for a judge evaluation."""
    targets_str = ", ".join(f'"{t}"' for t in targets)
    return (
        f'Evaluate the transition from task "{node_name}".\n'
        f"\n"
        f"Read the evaluation request from: {request_path}\n"
        f"Write your decision to: {decision_path}\n"
        f"\n"
        f"Your decision must be a JSON file with this format:\n"
        f'{{"decision": "<target>", "reasoning": "...", "confidence": 0.0-1.0}}\n'
        f"\n"
        f'Available targets: {targets_str}, "__none__"\n'
        f'Use "__none__" if no condition clearly matches.'
    )
