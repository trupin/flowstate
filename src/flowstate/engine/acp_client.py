"""ACP harness -- generic Agent Client Protocol client for any ACP-compatible agent.

Bridges the ACP callback model (``Client.session_update``) to Flowstate's
``AsyncGenerator[StreamEvent]`` model via an ``asyncio.Queue``.  Spawns an
ACP agent as a subprocess, communicates over JSON-RPC stdio, and translates
ACP session updates into the ``StreamEvent`` types consumed by the executor.

Supports two usage patterns:

1. **Long-lived sessions** (``start_session`` / ``prompt`` / ``interrupt``):
   The subprocess survives between ``prompt()`` calls, enabling multi-turn
   interaction and interrupt-without-kill.

2. **Convenience wrappers** (``run_task`` / ``run_task_resume``):
   One-shot lifecycle for backward compatibility.  Internally delegates to
   ``start_session()`` + ``prompt()``.

All ``acp`` imports are lazy (inside methods) to avoid import-time cost when
ACP harnesses are not in use.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from flowstate.engine.subprocess_mgr import (
    JudgeError,
    JudgeResult,
    StreamEvent,
    StreamEventType,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

# Timeout for ACP connection initialization (subprocess startup + handshake)
_ACP_INIT_TIMEOUT = 30.0
# Timeout for session creation/load after connection is initialized
_ACP_SESSION_TIMEOUT = 15.0

# Environment variables that must be inherited by the subprocess.
# The ACP library's default_environment() only passes HOME, PATH, SHELL, TERM,
# USER, LOGNAME on POSIX — stripping ANTHROPIC_API_KEY and config paths.
_REQUIRED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CONFIG_DIR",
    "XDG_CONFIG_HOME",
)


def _build_subprocess_env(extra_env: dict[str, str] | None = None) -> dict[str, str] | None:
    """Build subprocess env that includes critical variables from os.environ.

    Returns a dict to merge into ACP's default_environment(), or None if no
    extra variables are needed (lets ACP use its defaults only).
    """
    env: dict[str, str] = {}
    for key in _REQUIRED_ENV_VARS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if extra_env:
        env.update(extra_env)
    return env or None


# ---------------------------------------------------------------------------
# ACP update -> StreamEvent mapping
# ---------------------------------------------------------------------------


def _is_noise_chunk(text: str) -> bool:
    """Filter streaming fragments that add no informational value.

    Returns True for empty/whitespace-only text and single non-alphanumeric
    characters (e.g. ``.``, ``,``, ``:``).  Single alphanumeric characters and
    multi-character punctuation (``...``, ``---``) pass through because they
    could be the start of a streaming word or meaningful content.
    """
    trimmed = text.strip()
    if not trimmed:
        return True
    return len(trimmed) == 1 and not trimmed.isalnum()


def _extract_tool_call_content_text(content_list: list[Any] | None) -> str | None:
    """Extract human-readable text from an ACP ToolCall content list.

    Iterates over ``ContentToolCallContent``, ``FileEditToolCallContent``, and
    ``TerminalToolCallContent`` items and returns a concatenated text summary.
    Returns ``None`` if the content list is empty or contains no text.
    """
    if not content_list:
        return None
    parts: list[str] = []
    for item in content_list:
        # ContentToolCallContent wraps a content block (text, image, etc.)
        if hasattr(item, "content") and hasattr(item.content, "text"):
            parts.append(item.content.text)
        elif hasattr(item, "new_text"):
            # FileEditToolCallContent (Diff) -- show edited content
            parts.append(item.new_text)
        elif hasattr(item, "terminal_id"):
            # TerminalToolCallContent -- just note the terminal
            parts.append(f"[terminal:{item.terminal_id}]")
    return "\n".join(parts) if parts else None


def _serialize_raw_io(value: object) -> str | None:
    """Best-effort serialization of raw_input / raw_output from ACP.

    Returns a string representation, or ``None`` if the value is missing/empty.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value if value else None
    try:
        serialized = json.dumps(value, default=str)
        return serialized if serialized not in ("null", "{}") else None
    except (TypeError, ValueError):
        return str(value) or None


def _map_acp_update_to_stream_event(update: object) -> StreamEvent | None:
    """Convert an ACP session update notification to a Flowstate StreamEvent.

    Returns ``None`` for update types that have no meaningful StreamEvent
    equivalent (e.g. config changes, mode changes).
    """
    from acp.schema import (
        AgentMessageChunk,
        AgentPlanUpdate,
        AgentThoughtChunk,
        ToolCallProgress,
        ToolCallStart,
    )

    if isinstance(update, AgentMessageChunk):
        text = update.content.text if hasattr(update.content, "text") else str(update.content)
        if _is_noise_chunk(text):
            return None
        return StreamEvent(
            type=StreamEventType.ASSISTANT,
            content={"type": "assistant", "message": {"content": [{"text": text}]}},
            raw=json.dumps({"type": "assistant", "message": {"content": [{"text": text}]}}),
        )

    if isinstance(update, AgentThoughtChunk):
        text = update.content.text if hasattr(update.content, "text") else str(update.content)
        if _is_noise_chunk(text):
            return None
        return StreamEvent(
            type=StreamEventType.ASSISTANT,
            content={
                "type": "assistant",
                "thinking": True,
                "message": {"content": [{"text": text}]},
            },
            raw=json.dumps(
                {
                    "type": "assistant",
                    "thinking": True,
                    "message": {"content": [{"text": text}]},
                }
            ),
        )

    if isinstance(update, ToolCallStart):
        content_text = _extract_tool_call_content_text(getattr(update, "content", None))
        raw_input = _serialize_raw_io(getattr(update, "raw_input", None))
        kind = getattr(update, "kind", None)
        payload: dict[str, Any] = {
            "type": "tool_use",
            "tool_call_id": update.tool_call_id,
            "title": update.title,
            "status": update.status,
        }
        if kind is not None:
            payload["kind"] = kind
        if raw_input is not None:
            payload["raw_input"] = raw_input
        if content_text is not None:
            payload["content"] = content_text
        return StreamEvent(
            type=StreamEventType.TOOL_USE,
            content=payload,
            raw=json.dumps(payload),
        )

    if isinstance(update, ToolCallProgress):
        content_text = _extract_tool_call_content_text(getattr(update, "content", None))
        raw_output = _serialize_raw_io(getattr(update, "raw_output", None))
        kind = getattr(update, "kind", None)
        title = getattr(update, "title", None)
        status = getattr(update, "status", None)
        payload = {
            "type": "tool_result",
            "tool_call_id": update.tool_call_id,
            "status": status,
            "title": title,
        }
        if kind is not None:
            payload["kind"] = kind
        if raw_output is not None:
            payload["raw_output"] = raw_output
        if content_text is not None:
            payload["content"] = content_text
        return StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            content=payload,
            raw=json.dumps(payload),
        )

    if isinstance(update, AgentPlanUpdate):
        entries = [{"title": e.title, "status": e.status} for e in (update.entries or [])]
        return StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"type": "plan", "entries": entries},
            raw=json.dumps({"type": "plan", "entries": entries}),
        )

    # Unknown or irrelevant update types (config, mode, usage, etc.)
    logger.debug("Skipping unmapped ACP update type: %s", type(update).__name__)
    return None


# ---------------------------------------------------------------------------
# Bridge: ACP Client callbacks -> asyncio.Queue
# ---------------------------------------------------------------------------


class _AcpBridgeClient:
    """ACP Client implementation that forwards session updates to a queue.

    Satisfies the ``acp.Client`` protocol structurally (duck typing).
    ``session_update`` pushes mapped ``StreamEvent`` objects; ``request_permission``
    auto-approves all tool calls.
    """

    def __init__(self, queue: asyncio.Queue[StreamEvent | None]) -> None:
        self._queue = queue
        self._conn: Any = None

    def on_connect(self, conn: Any) -> None:
        """Called when the connection is established."""
        self._conn = conn

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        """Map an ACP session update to a StreamEvent and enqueue it.

        Wraps in try/except because the ACP library's notification dispatcher
        uses ``contextlib.suppress(Exception)`` which silently swallows errors.
        """
        try:
            update_type = type(update).__name__
            # Log every event for debugging ACP bridge issues.  Include key
            # fields that help diagnose missing-content problems.
            if logger.isEnabledFor(logging.DEBUG):
                detail_parts = [f"type={update_type}"]
                if hasattr(update, "tool_call_id"):
                    detail_parts.append(f"tool_call_id={update.tool_call_id}")
                if hasattr(update, "title"):
                    detail_parts.append(f"title={update.title!r}")
                if hasattr(update, "status"):
                    detail_parts.append(f"status={update.status}")
                if hasattr(update, "kind"):
                    detail_parts.append(f"kind={update.kind}")
                has_content = hasattr(update, "content") and update.content is not None
                detail_parts.append(f"has_content={has_content}")
                has_raw_output = hasattr(update, "raw_output") and update.raw_output is not None
                detail_parts.append(f"has_raw_output={has_raw_output}")
                has_raw_input = hasattr(update, "raw_input") and update.raw_input is not None
                detail_parts.append(f"has_raw_input={has_raw_input}")
                logger.debug(
                    "ACP session_update [%s]: %s",
                    session_id,
                    ", ".join(detail_parts),
                )
            event = _map_acp_update_to_stream_event(update)
            if event is not None:
                self._queue.put_nowait(event)
        except Exception:
            logger.exception("Error in session_update for session %s", session_id)
            raise

    async def request_permission(
        self,
        options: Any,
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> Any:
        """Auto-approve all permission requests.

        Returns a ``RequestPermissionResponse`` selecting the first
        ``allow_once`` or ``allow_always`` option, or the first option if none.
        """
        from acp.schema import AllowedOutcome, RequestPermissionResponse

        # Find the first allow option, or just use the first option
        selected_id: str = options[0].option_id if options else "allow"
        for opt in options:
            kind = getattr(opt, "kind", "")
            if kind in ("allow_once", "allow_always"):
                selected_id = opt.option_id
                break
        return RequestPermissionResponse(
            outcome=AllowedOutcome(option_id=selected_id, outcome="selected"),
        )

    # Stubs for other Client protocol methods -- not invoked by the agent
    # during normal prompt/session flow, but required for protocol compliance.
    async def write_text_file(self, content: str, path: str, session_id: str, **kwargs: Any) -> Any:
        return None

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> Any:
        return None

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: Any = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> Any:
        return None

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        return None

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        return None

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        return None

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None


# ---------------------------------------------------------------------------
# _AcpSession -- tracks a long-lived ACP session
# ---------------------------------------------------------------------------


class _AcpSession:
    """Tracks an active ACP session for prompt/cancel/kill support.

    Holds references to the ACP connection, subprocess, event queue, bridge
    client, and the ACP-assigned session ID.  The subprocess survives between
    ``prompt()`` calls.
    """

    def __init__(
        self,
        conn: object,
        process: object,
        queue: asyncio.Queue[StreamEvent | None],
        bridge: _AcpBridgeClient,
        acp_session_id: str,
    ) -> None:
        self.conn = conn
        self.process = process
        self.queue = queue
        self.bridge = bridge
        self.acp_session_id = acp_session_id

    @property
    def process_alive(self) -> bool:
        """Return True if the subprocess is still running."""
        returncode = getattr(self.process, "returncode", None)
        return returncode is None

    async def cancel(self) -> None:
        """Cancel the current prompt via ACP without killing the subprocess."""
        from acp import RequestError

        try:
            if hasattr(self.conn, "cancel"):
                await self.conn.cancel(session_id=self.acp_session_id)  # type: ignore[union-attr]
        except (RequestError, Exception):
            logger.debug("session/cancel failed for session %s", self.acp_session_id)

    async def cancel_and_terminate(self) -> None:
        """Send session/cancel and terminate the process."""
        await self.cancel()

        # Terminate the subprocess
        if hasattr(self.process, "terminate"):
            self.process.terminate()  # type: ignore[union-attr]
            try:
                await asyncio.wait_for(
                    self.process.wait(),  # type: ignore[union-attr]
                    timeout=5.0,
                )
            except TimeoutError:
                if hasattr(self.process, "kill"):
                    self.process.kill()  # type: ignore[union-attr]

        # Signal the queue to stop
        self.queue.put_nowait(None)


# ---------------------------------------------------------------------------
# AcpHarness
# ---------------------------------------------------------------------------

# Sentinel for the METHOD_NOT_FOUND JSON-RPC error code
_METHOD_NOT_FOUND_CODE = -32601


class AcpSessionError(Exception):
    """Raised when an ACP session operation fails."""


class AcpHarness:
    """Generic ACP client harness -- works with any ACP-compatible agent.

    Spawns the agent as a subprocess, communicates via ACP JSON-RPC over stdio,
    and translates ACP session updates into Flowstate ``StreamEvent`` objects.

    Satisfies the ``Harness`` Protocol structurally (duck typing).

    Supports two usage modes:

    1. **Long-lived sessions**: ``start_session()`` spawns a subprocess and
       initializes the ACP connection.  ``prompt()`` sends messages to the
       existing session (async generator).  ``interrupt()`` cancels the
       current prompt without killing the subprocess.  ``kill()`` terminates
       the subprocess.

    2. **Convenience wrappers**: ``run_task()`` and ``run_task_resume()``
       combine ``start_session()`` + ``prompt()`` for backward compatibility.
    """

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        init_timeout: float = _ACP_INIT_TIMEOUT,
    ) -> None:
        self._command = command
        self._env = env
        self._init_timeout = init_timeout
        # Track active sessions for prompt/cancel/kill
        self._sessions: dict[str, _AcpSession] = {}
        # Track spawn context managers so they stay alive for long-lived sessions
        self._spawn_contexts: dict[str, Any] = {}
        # Map Flowstate session IDs → ACP session IDs for resume across subprocesses
        self._acp_id_map: dict[str, str] = {}

    @property
    def command(self) -> list[str]:
        """Return a copy of the command used to spawn the agent subprocess."""
        return list(self._command)

    @property
    def env(self) -> dict[str, str] | None:
        """Return a copy of the extra environment variables, or None."""
        return dict(self._env) if self._env else None

    # ------------------------------------------------------------------ #
    # Long-lived session API
    # ------------------------------------------------------------------ #

    async def start_session(self, workspace: str, session_id: str) -> None:
        """Spawn subprocess, initialize ACP, and create a new session.

        The subprocess stays alive until ``kill()`` is called.  Multiple
        ``prompt()`` calls can be made against the same session.

        If a session with the given *session_id* already exists, it is killed
        first to avoid resource leaks.
        """
        from acp import PROTOCOL_VERSION, spawn_agent_process

        # Kill existing session with same ID to avoid collision
        if session_id in self._sessions:
            await self.kill(session_id)

        cmd_name = self._command[0]
        cmd_args = self._command[1:]

        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        bridge = _AcpBridgeClient(queue)

        # Spawn the agent subprocess.  We need to enter the context manager
        # manually and keep it alive for the session's lifetime.
        ctx = spawn_agent_process(
            bridge,
            cmd_name,
            *cmd_args,
            env=_build_subprocess_env(self._env),
            cwd=workspace,
        )
        conn, process = await ctx.__aenter__()
        self._spawn_contexts[session_id] = ctx

        try:
            # Health check: verify subprocess didn't exit immediately
            await asyncio.sleep(0.1)
            if process.returncode is not None:
                raise AcpSessionError(
                    f"ACP subprocess exited immediately with code {process.returncode}. "
                    f"Command: {self._command}"
                )

            # Initialize ACP connection (with timeout)
            try:
                await asyncio.wait_for(
                    conn.initialize(protocol_version=PROTOCOL_VERSION),
                    timeout=self._init_timeout,
                )
            except TimeoutError:
                raise AcpSessionError(
                    f"ACP initialize timed out after {self._init_timeout}s — "
                    f"subprocess may not support ACP protocol"
                ) from None

            # Create new session (with timeout)
            try:
                new_resp = await asyncio.wait_for(
                    conn.new_session(cwd=workspace),
                    timeout=_ACP_SESSION_TIMEOUT,
                )
            except TimeoutError:
                raise AcpSessionError(
                    f"ACP new_session timed out after {_ACP_SESSION_TIMEOUT}s"
                ) from None
            acp_session_id = new_resp.session_id

            session = _AcpSession(
                conn=conn,
                process=process,
                queue=queue,
                bridge=bridge,
                acp_session_id=acp_session_id,
            )
            self._sessions[session_id] = session
        except Exception:
            # Cleanup on failure
            await ctx.__aexit__(None, None, None)
            self._spawn_contexts.pop(session_id, None)
            raise

    async def prompt(self, session_id: str, message: str) -> AsyncGenerator[StreamEvent, None]:
        """Send a prompt to an existing session and yield streamed events.

        The subprocess must have been started via ``start_session()``.
        Events arrive via the bridge callback queue and are yielded as they
        become available.

        Raises ``AcpSessionError`` if the session does not exist or the
        subprocess has crashed.
        """
        from acp import text_block

        session = self._sessions.get(session_id)
        if session is None:
            raise AcpSessionError(f"No active session with id '{session_id}'")

        if not session.process_alive:
            raise AcpSessionError(
                f"Session '{session_id}' subprocess has exited "
                f"(returncode={getattr(session.process, 'returncode', '?')})"
            )

        # Drain stale sentinels from previous cancel/interrupt cycles
        while not session.queue.empty():
            try:
                session.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        try:
            async for event in self._prompt_and_stream(
                session.conn,
                session.queue,
                {"prompt": [text_block(message)], "session_id": session.acp_session_id},
            ):
                yield event
        except Exception as e:
            if not isinstance(e, GeneratorExit | StopAsyncIteration | AcpSessionError):
                logger.error("ACP prompt error for session %s: %s", session_id, e)
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    content={"type": "error", "error": {"message": str(e)}},
                    raw=json.dumps({"type": "error", "error": {"message": str(e)}}),
                )
                yield StreamEvent(
                    type=StreamEventType.SYSTEM,
                    content={
                        "event": "process_exit",
                        "exit_code": 1,
                        "stderr": str(e),
                    },
                    raw="Process exited with code 1",
                )
            else:
                raise

    async def interrupt(self, session_id: str) -> None:
        """Cancel the current prompt without killing the subprocess.

        After interrupt, the session is still alive and can accept new
        ``prompt()`` calls.
        """
        session = self._sessions.get(session_id)
        if session is not None:
            await session.cancel()

    # ------------------------------------------------------------------ #
    # Lifecycle management
    # ------------------------------------------------------------------ #

    async def kill(self, session_id: str) -> None:
        """Terminate the subprocess entirely and clean up session state."""
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.cancel_and_terminate()

        # Exit the spawn context manager to clean up resources
        ctx = self._spawn_contexts.pop(session_id, None)
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                logger.debug("Error cleaning up spawn context for session %s", session_id)

    # ------------------------------------------------------------------ #
    # Convenience wrappers (backward-compatible one-shot API)
    # ------------------------------------------------------------------ #

    async def run_task(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Spawn agent, create session, send prompt, and stream events.

        Convenience wrapper: ``start_session()`` + ``prompt()``.
        The caller is responsible for calling ``kill()`` when done.
        """
        async for event in self._run_acp_session(
            prompt=prompt,
            workspace=workspace,
            session_id=session_id,
            resume=False,
        ):
            yield event

    async def run_task_resume(
        self,
        prompt: str,
        workspace: str,
        resume_session_id: str,
        *,
        skip_permissions: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Resume a previous session if supported, otherwise start fresh.

        Convenience wrapper for backward compatibility.
        """
        async for event in self._run_acp_session(
            prompt=prompt,
            workspace=workspace,
            session_id=resume_session_id,
            resume=True,
        ):
            yield event

    async def run_judge(
        self,
        prompt: str,
        workspace: str,
        *,
        skip_permissions: bool = False,
    ) -> JudgeResult:
        """Run a judge evaluation: send prompt, collect text, parse as JSON."""
        collected_text = ""
        session_id = f"judge-{id(self)}"

        async for event in self._run_acp_session(
            prompt=prompt,
            workspace=workspace,
            session_id=session_id,
            resume=False,
        ):
            if event.type == StreamEventType.ASSISTANT:
                # Extract text from the content
                msg = event.content.get("message", {})
                for block in msg.get("content", []):
                    if "text" in block:
                        collected_text += block["text"]
            elif event.type == StreamEventType.RESULT:
                result_text = event.content.get("result", "")
                if result_text:
                    collected_text += result_text

        try:
            data = json.loads(collected_text)
            return JudgeResult(
                decision=data["decision"],
                reasoning=data["reasoning"],
                confidence=float(data["confidence"]),
                raw_output=collected_text,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise JudgeError(
                f"Failed to parse judge output: {e}",
                exit_code=0,
                stderr="",
            ) from e

    # ------------------------------------------------------------------ #
    # Internal: concurrent prompt + stream
    # ------------------------------------------------------------------ #

    async def _prompt_and_stream(
        self,
        conn: Any,
        queue: asyncio.Queue[StreamEvent | None],
        prompt_args: dict[str, Any],
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run conn.prompt() concurrently while yielding events as they arrive.

        Launches ``conn.prompt()`` as a background task and drains the event
        queue in real-time.  When the prompt finishes, a ``None`` sentinel is
        pushed to unblock the drain loop.  After draining, RESULT and SYSTEM
        events are emitted based on the prompt's stop_reason.

        Exceptions from ``conn.prompt()`` are re-raised so callers can handle
        them (e.g. emit ERROR + SYSTEM events).
        """
        prompt_task = asyncio.create_task(conn.prompt(**prompt_args))

        def _on_done(task: asyncio.Task[Any]) -> None:
            queue.put_nowait(None)  # sentinel: prompt finished

        prompt_task.add_done_callback(_on_done)

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not prompt_task.done():
                prompt_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prompt_task

        # Drain any events that arrived between sentinel and now
        while not queue.empty():
            event = queue.get_nowait()
            if event is not None:
                yield event

        # Re-raise exception from prompt task if it failed
        prompt_response = prompt_task.result()

        # Yield RESULT + SYSTEM events based on stop_reason
        stop_reason = prompt_response.stop_reason
        if stop_reason == "cancelled":
            yield StreamEvent(
                type=StreamEventType.SYSTEM,
                content={
                    "event": "process_exit",
                    "exit_code": -1,
                    "stderr": "Agent session cancelled",
                },
                raw="Agent session cancelled",
            )
        else:
            yield StreamEvent(
                type=StreamEventType.RESULT,
                content={
                    "type": "result",
                    "result": "",
                    "stop_reason": stop_reason,
                },
                raw=json.dumps(
                    {
                        "type": "result",
                        "result": "",
                        "stop_reason": stop_reason,
                    }
                ),
            )
            yield StreamEvent(
                type=StreamEventType.SYSTEM,
                content={
                    "event": "process_exit",
                    "exit_code": 0,
                    "stderr": "",
                },
                raw="Process exited with code 0",
            )

    # ------------------------------------------------------------------ #
    # Internal: one-shot ACP session (used by run_task / run_task_resume)
    # ------------------------------------------------------------------ #

    async def _run_acp_session(
        self,
        prompt: str,
        workspace: str,
        session_id: str,
        *,
        resume: bool,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Core ACP session lifecycle (one-shot mode).

        1. Spawn agent subprocess via ``acp.spawn_agent_process``
        2. ``initialize()`` + ``new_session()`` (or ``load_session()`` for resume)
        3. ``prompt()`` -- events arrive via ``session_update`` callback -> queue
        4. Yield events from queue
        5. On completion yield RESULT + SYSTEM process_exit events
        """
        from acp import PROTOCOL_VERSION, RequestError, spawn_agent_process, text_block

        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        bridge = _AcpBridgeClient(queue)

        cmd_name = self._command[0]
        cmd_args = self._command[1:]

        try:
            async with spawn_agent_process(
                bridge,
                cmd_name,
                *cmd_args,
                env=_build_subprocess_env(self._env),
                cwd=workspace,
            ) as (conn, process):
                # Track for kill()
                acp_session = _AcpSession(
                    conn=conn,
                    process=process,
                    queue=queue,
                    bridge=bridge,
                    acp_session_id="",
                )
                self._sessions[session_id] = acp_session

                try:
                    # Health check: verify subprocess didn't exit immediately
                    await asyncio.sleep(0.1)
                    if process.returncode is not None:
                        raise AcpSessionError(
                            f"ACP subprocess exited immediately with code "
                            f"{process.returncode}. Command: {self._command}"
                        )

                    # Initialize the connection (with timeout)
                    try:
                        await asyncio.wait_for(
                            conn.initialize(protocol_version=PROTOCOL_VERSION),
                            timeout=self._init_timeout,
                        )
                    except TimeoutError:
                        raise AcpSessionError(
                            f"ACP initialize timed out after {self._init_timeout}s — "
                            f"subprocess may not support ACP protocol"
                        ) from None

                    # Create or load session
                    acp_session_id: str
                    if resume:
                        # Resolve Flowstate session ID → ACP session ID.
                        # The ACP session ID was generated by new_session() in a
                        # previous subprocess.  We must use it (not our UUID) so
                        # load_session() can find the persisted session on disk.
                        resume_acp_id = self._acp_id_map.get(session_id, session_id)
                        try:
                            resp = await asyncio.wait_for(
                                conn.load_session(cwd=workspace, session_id=resume_acp_id),
                                timeout=_ACP_SESSION_TIMEOUT,
                            )
                            # load_session may return None on some agents
                            if resp is not None:
                                acp_session_id = resume_acp_id
                            else:
                                # Agent returned None -- fall back to new session
                                new_resp = await asyncio.wait_for(
                                    conn.new_session(cwd=workspace),
                                    timeout=_ACP_SESSION_TIMEOUT,
                                )
                                acp_session_id = new_resp.session_id
                        except RequestError as e:
                            if e.code == _METHOD_NOT_FOUND_CODE:
                                # Agent does not support session/load
                                logger.info(
                                    "Agent does not support session/load, "
                                    "falling back to new session"
                                )
                                new_resp = await asyncio.wait_for(
                                    conn.new_session(cwd=workspace),
                                    timeout=_ACP_SESSION_TIMEOUT,
                                )
                                acp_session_id = new_resp.session_id
                            else:
                                raise
                        except TimeoutError:
                            raise AcpSessionError(
                                f"ACP session load/create timed out after {_ACP_SESSION_TIMEOUT}s"
                            ) from None
                    else:
                        try:
                            new_resp = await asyncio.wait_for(
                                conn.new_session(cwd=workspace),
                                timeout=_ACP_SESSION_TIMEOUT,
                            )
                        except TimeoutError:
                            raise AcpSessionError(
                                f"ACP new_session timed out after {_ACP_SESSION_TIMEOUT}s"
                            ) from None
                        acp_session_id = new_resp.session_id
                        # Remember the mapping so run_task_resume() can find it
                        self._acp_id_map[session_id] = acp_session_id

                    # Update the session's ACP session ID
                    acp_session.acp_session_id = acp_session_id

                    # Stream events in real-time while prompt() runs concurrently
                    async for event in self._prompt_and_stream(
                        conn,
                        queue,
                        {"prompt": [text_block(prompt)], "session_id": acp_session_id},
                    ):
                        yield event

                finally:
                    self._sessions.pop(session_id, None)

        except Exception as e:
            # Agent crashed or connection failed -- emit error + exit event
            if not isinstance(e, GeneratorExit | StopAsyncIteration):
                logger.error("ACP agent error: %s", e)
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    content={"type": "error", "error": {"message": str(e)}},
                    raw=json.dumps({"type": "error", "error": {"message": str(e)}}),
                )
                yield StreamEvent(
                    type=StreamEventType.SYSTEM,
                    content={
                        "event": "process_exit",
                        "exit_code": 1,
                        "stderr": str(e),
                    },
                    raw="Process exited with code 1",
                )
            else:
                raise
