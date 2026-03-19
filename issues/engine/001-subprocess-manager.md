# [ENGINE-001] Subprocess Manager (Claude Code lifecycle)

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: SHARED-001
- Blocks: ENGINE-004, ENGINE-005

## Spec References
- specs.md Section 9.1 — "Task Subprocess Invocation"
- specs.md Section 9.2 — "Output Capture"
- specs.md Section 9.3 — "Session Management"
- specs.md Section 9.4 — "Error Detection"
- agents/03-engine.md — "Claude Code Subprocess Management"

## Summary
Implement the subprocess manager that launches and manages Claude Code subprocesses for task execution and judge evaluation. This is the lowest-level building block of the engine — it handles spawning `claude` processes with the correct CLI flags, parsing their streaming JSON output line by line, and yielding typed event objects to callers. Three invocation patterns are supported: fresh task session (`run_task`), resumed session (`run_task_resume`), and judge evaluation (`run_judge`).

## Acceptance Criteria
- [ ] File `src/flowstate/engine/subprocess_mgr.py` exists and is importable
- [ ] File `src/flowstate/engine/__init__.py` exists
- [ ] `SubprocessManager` class is implemented with three public methods:
  - `run_task(prompt, workspace, session_id) -> AsyncGenerator[StreamEvent, None]`
  - `run_task_resume(prompt, workspace, resume_session_id) -> AsyncGenerator[StreamEvent, None]`
  - `run_judge(prompt, workspace) -> JudgeResult`
- [ ] `StreamEvent` dataclass is defined with fields: `type` (str enum), `content` (dict), `raw` (str)
- [ ] `StreamEventType` enum covers: `ASSISTANT`, `TOOL_USE`, `TOOL_RESULT`, `RESULT`, `ERROR`, `SYSTEM`
- [ ] `JudgeResult` dataclass is defined with fields: `decision` (str), `reasoning` (str), `confidence` (float), `raw_output` (str)
- [ ] `run_task` constructs the command: `claude -p "<prompt>" --output-format stream-json`
- [ ] `run_task_resume` constructs: `claude -p "<prompt>" --output-format stream-json --resume <session_id>`
- [ ] `run_judge` constructs: `claude -p "<prompt>" --output-format json --permission-mode plan --model sonnet`
- [ ] All subprocesses are started with `cwd=workspace`
- [ ] Stream-json output is parsed line by line from stdout, each line parsed as JSON
- [ ] Each parsed line is categorized by its `type` field into the correct `StreamEventType`
- [ ] On process exit, a final `StreamEvent` is yielded with exit code information
- [ ] `run_judge` reads full stdout, parses as JSON, extracts decision/reasoning/confidence
- [ ] `run_judge` raises `JudgeError` on non-zero exit code or unparseable output
- [ ] Stderr is captured and available in error messages
- [ ] A `kill(session_id)` method exists to terminate a running subprocess
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/__init__.py` — empty package init
- `src/flowstate/engine/subprocess_mgr.py` — subprocess manager implementation
- `tests/engine/__init__.py` — empty package init
- `tests/engine/test_subprocess_mgr.py` — tests

### Key Implementation Details

#### Data Types

```python
from dataclasses import dataclass
from enum import Enum


class StreamEventType(str, Enum):
    ASSISTANT = "assistant"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    RESULT = "result"
    ERROR = "error"
    SYSTEM = "system"  # internal events like process exit


@dataclass
class StreamEvent:
    type: StreamEventType
    content: dict  # the full parsed JSON object from stdout
    raw: str       # the original line from stdout


@dataclass
class JudgeResult:
    decision: str
    reasoning: str
    confidence: float
    raw_output: str


class SubprocessError(Exception):
    """Raised when a subprocess fails unexpectedly."""
    def __init__(self, message: str, exit_code: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


class JudgeError(SubprocessError):
    """Raised when a judge subprocess fails or returns unparseable output."""
    pass
```

#### SubprocessManager Class

```python
class SubprocessManager:
    def __init__(self):
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        # Maps session_id to running process for kill support

    async def run_task(
        self, prompt: str, workspace: str, session_id: str
    ) -> AsyncGenerator[StreamEvent, None]:
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json"]
        async for event in self._run_streaming(cmd, workspace, session_id):
            yield event

    async def run_task_resume(
        self, prompt: str, workspace: str, resume_session_id: str
    ) -> AsyncGenerator[StreamEvent, None]:
        cmd = [
            "claude", "-p", prompt,
            "--output-format", "stream-json",
            "--resume", resume_session_id,
        ]
        async for event in self._run_streaming(cmd, workspace, resume_session_id):
            yield event

    async def run_judge(self, prompt: str, workspace: str) -> JudgeResult:
        cmd = [
            "claude", "-p", prompt,
            "--output-format", "json",
            "--permission-mode", "plan",
            "--model", "sonnet",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout_text = stdout_bytes.decode()
        stderr_text = stderr_bytes.decode()

        if proc.returncode != 0:
            raise JudgeError(
                f"Judge subprocess exited with code {proc.returncode}: {stderr_text}",
                exit_code=proc.returncode,
                stderr=stderr_text,
            )

        try:
            data = json.loads(stdout_text)
            # The JSON output from claude --output-format json wraps the result;
            # extract the actual content. The exact structure should be validated
            # at implementation time against real claude output.
            return JudgeResult(
                decision=data["decision"],
                reasoning=data["reasoning"],
                confidence=float(data["confidence"]),
                raw_output=stdout_text,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise JudgeError(
                f"Failed to parse judge output: {e}",
                exit_code=proc.returncode,
                stderr=stderr_text,
            )

    async def kill(self, session_id: str) -> None:
        proc = self._processes.pop(session_id, None)
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
```

#### Streaming Output Parser (`_run_streaming`)

```python
async def _run_streaming(
    self, cmd: list[str], workspace: str, session_id: str
) -> AsyncGenerator[StreamEvent, None]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    self._processes[session_id] = proc

    try:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            raw = line.decode().rstrip("\n")
            if not raw:
                continue
            try:
                data = json.loads(raw)
                event_type = self._classify_event(data.get("type", ""))
                yield StreamEvent(type=event_type, content=data, raw=raw)
            except json.JSONDecodeError:
                # Non-JSON line — emit as system event
                yield StreamEvent(
                    type=StreamEventType.SYSTEM,
                    content={"message": raw},
                    raw=raw,
                )

        # Wait for process to finish
        await proc.wait()

        # Read stderr
        stderr_text = ""
        if proc.stderr:
            stderr_bytes = await proc.stderr.read()
            stderr_text = stderr_bytes.decode()

        # Emit exit event
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={
                "event": "process_exit",
                "exit_code": proc.returncode,
                "stderr": stderr_text,
            },
            raw=f"Process exited with code {proc.returncode}",
        )
    finally:
        self._processes.pop(session_id, None)

def _classify_event(self, type_str: str) -> StreamEventType:
    mapping = {
        "assistant": StreamEventType.ASSISTANT,
        "tool_use": StreamEventType.TOOL_USE,
        "tool_result": StreamEventType.TOOL_RESULT,
        "result": StreamEventType.RESULT,
        "error": StreamEventType.ERROR,
    }
    return mapping.get(type_str, StreamEventType.SYSTEM)
```

### Edge Cases
- **Empty stdout lines**: Skip blank lines between JSON objects.
- **Non-JSON stdout lines**: Classify as `SYSTEM` events rather than crashing.
- **Process killed externally**: The `readline()` loop exits cleanly when stdout closes. The exit event will have a non-zero (signal-based) return code.
- **Subprocess not found**: If `claude` is not on PATH, `create_subprocess_exec` raises `FileNotFoundError`. Let it propagate — the executor handles it.
- **Very long prompts**: The prompt is passed via `-p` flag. If it's too long for the command line, this will fail. This is a known limitation documented in the spec — the engine does not currently use stdin for prompt delivery.
- **Concurrent kill and read**: The `kill` method removes the process from the dict and terminates it. The streaming generator's `finally` block also removes it. Use `pop` with default to avoid KeyError.
- **Stderr handling**: For streaming tasks, stderr is read after process exit (not during streaming) to avoid deadlocks.

## Testing Strategy

Create `tests/engine/test_subprocess_mgr.py`:

1. **test_run_task_command_construction** — Mock `asyncio.create_subprocess_exec`, verify the exact command and kwargs passed (flags, cwd, stdout/stderr pipes).

2. **test_run_task_resume_command_construction** — Same as above but verify `--resume <session_id>` is included.

3. **test_run_judge_command_construction** — Verify `--output-format json`, `--permission-mode plan`, `--model sonnet` flags.

4. **test_stream_event_parsing** — Create a mock process that outputs multiple JSON lines (`assistant`, `tool_use`, `tool_result`, `result`). Verify each is classified correctly and yielded as the right `StreamEventType`.

5. **test_stream_error_event** — Mock process outputs an `error` type JSON line. Verify it becomes `StreamEventType.ERROR`.

6. **test_stream_non_json_line** — Mock process outputs a non-JSON line. Verify it becomes `StreamEventType.SYSTEM`.

7. **test_stream_exit_event** — Verify the final event after process exit has `event: "process_exit"` and the correct exit code.

8. **test_run_judge_success** — Mock process returns valid JSON with decision/reasoning/confidence. Verify `JudgeResult` fields.

9. **test_run_judge_non_zero_exit** — Mock process exits with code 1. Verify `JudgeError` is raised with exit code and stderr.

10. **test_run_judge_invalid_json** — Mock process returns non-JSON output. Verify `JudgeError` is raised.

11. **test_run_judge_missing_fields** — Mock process returns JSON missing required fields. Verify `JudgeError`.

12. **test_kill_running_process** — Start a mock process, call `kill`, verify `terminate()` was called.

13. **test_kill_nonexistent_session** — Call `kill` with unknown session_id, verify no error (no-op).

Use `unittest.mock.AsyncMock` and `unittest.mock.patch` to mock `asyncio.create_subprocess_exec`. Create helper functions to build mock processes with predetermined stdout/stderr/returncode.
