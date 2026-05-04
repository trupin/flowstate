"""Playwright demo: unit test generation flow with visible browser and video.

Runs the happy path through the actual UI with:
- 1920x1080 visible browser
- ~10s per node with realistic agent-style console output
- Shows the .flow source code by slowly scrolling through the DSL popup
- Camera zooms to each running node
- Video recording

Usage:
    uv run python demo/run_demo.py
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

os.environ["FLOWSTATE_TEST_MODE"] = "1"

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from flowstate.config import FlowstateConfig  # noqa: E402
from flowstate.engine.subprocess_mgr import (  # noqa: E402
    StreamEvent,
    StreamEventType,
    SubprocessManager,
)
from flowstate.server.app import create_app  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

FLOW_FILE = Path(__file__).parent / "unit_test_gen.flow"
VIDEO_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Realistic agent console output per node (~10s each)
# ---------------------------------------------------------------------------

NODE_LOGS: dict[str, list[tuple[str, dict]]] = {
    "receive_ticket": [
        (
            "thinking",
            {"thinking": "I need to read the Jira ticket and understand the requirements."},
        ),
        (
            "tool_use",
            {"name": "Read", "id": "tu_01", "input": {"file_path": "/tickets/PROJ-4821.json"}},
        ),
        (
            "tool_result",
            {
                "content": [
                    {
                        "text": '{"id": "PROJ-4821", "title": "Add input validation to user registration", "priority": "High"}'
                    }
                ]
            },
        ),
        (
            "assistant",
            {
                "text": "Got the ticket. PROJ-4821 is about adding input validation to the user registration endpoint."
            },
        ),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_02",
                "input": {"command": "find src/auth -name '*.py' | head -10"},
            },
        ),
        (
            "tool_result",
            {
                "content": [
                    {
                        "text": "src/auth/__init__.py\nsrc/auth/registration.py\nsrc/auth/validators.py\nsrc/auth/models.py"
                    }
                ]
            },
        ),
        (
            "assistant",
            {
                "text": "Found the relevant source files. I'll start by analyzing the registration module."
            },
        ),
        (
            "thinking",
            {
                "thinking": "The ticket mentions 4 acceptance criteria. Let me extract them and plan my approach."
            },
        ),
        (
            "assistant",
            {"text": "Acceptance criteria parsed. Ready to analyze the code for defects."},
        ),
    ],
    "analyze_code": [
        (
            "thinking",
            {
                "thinking": "Let me scan the registration code for potential issues before generating tests."
            },
        ),
        (
            "tool_use",
            {"name": "Read", "id": "tu_03", "input": {"file_path": "src/auth/registration.py"}},
        ),
        (
            "tool_result",
            {
                "content": [
                    {
                        "text": "class RegistrationService:\n    def validate_email(self, email: str) -> bool: ...\n    def validate_password(self, pwd: str) -> bool: ...\n    def register_user(self, data: dict) -> User: ..."
                    }
                ]
            },
        ),
        (
            "assistant",
            {"text": "Analyzing validate_email() — checks format but missing domain blocklist."},
        ),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_04",
                "input": {"command": "uv run ruff check src/auth/registration.py"},
            },
        ),
        ("tool_result", {"content": [{"text": "All checks passed!"}]}),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_05",
                "input": {"command": "uv run pytest tests/auth/ --co -q 2>/dev/null | wc -l"},
            },
        ),
        ("tool_result", {"content": [{"text": "7 tests collected"}]}),
        (
            "thinking",
            {
                "thinking": "Only 7 tests for 3 functions — coverage is low but no critical defects. Proceeding to test generation."
            },
        ),
        (
            "assistant",
            {
                "text": "Static analysis clean, no critical defects found. Current test coverage is low (7 tests). Proceeding to generate comprehensive unit tests."
            },
        ),
    ],
    "generate_tests": [
        (
            "thinking",
            {
                "thinking": "I need to generate thorough unit tests for all three public methods in registration.py."
            },
        ),
        (
            "tool_use",
            {"name": "Read", "id": "tu_06", "input": {"file_path": "src/auth/registration.py"}},
        ),
        (
            "tool_result",
            {
                "content": [
                    {
                        "text": "def validate_email(self, email: str) -> bool:\n    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'\n    return bool(re.match(pattern, email))"
                    }
                ]
            },
        ),
        ("assistant", {"text": "Generating parametrized tests for validate_email()..."}),
        (
            "tool_use",
            {
                "name": "Write",
                "id": "tu_07",
                "input": {
                    "file_path": "tests/auth/test_registration.py",
                    "content": "import pytest\nfrom auth.registration import RegistrationService\n\nclass TestValidateEmail:\n    @pytest.mark.parametrize('email,valid', [\n        ('user@example.com', True),\n        ('bad-email', False),\n    ])\n    def test_email_validation(self, email, valid): ...",
                },
            },
        ),
        (
            "assistant",
            {
                "text": "Writing test_validate_password() — covering length, complexity, and common passwords..."
            },
        ),
        (
            "tool_use",
            {
                "name": "Write",
                "id": "tu_08",
                "input": {
                    "file_path": "tests/auth/test_registration.py",
                    "content": "(appending password tests...)",
                },
            },
        ),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_09",
                "input": {"command": "uv run pytest tests/auth/test_registration.py -v"},
            },
        ),
        (
            "tool_result",
            {
                "content": [
                    {
                        "text": "tests/auth/test_registration.py::TestValidateEmail::test_email_validation[user@example.com-True] PASSED\n...\n18 passed in 0.43s"
                    }
                ]
            },
        ),
        (
            "assistant",
            {
                "text": "All 18 generated tests pass. Test coverage for registration module: 43% → 89%."
            },
        ),
    ],
    "open_pr": [
        (
            "thinking",
            {"thinking": "Time to create a PR with the new tests. I'll branch, commit, and push."},
        ),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_10",
                "input": {"command": "git checkout -b feat/PROJ-4821-registration-tests"},
            },
        ),
        (
            "tool_result",
            {"content": [{"text": "Switched to a new branch 'feat/PROJ-4821-registration-tests'"}]},
        ),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_11",
                "input": {
                    "command": "git add tests/auth/test_registration.py && git commit -m 'Add unit tests for registration validation'"
                },
            },
        ),
        (
            "tool_result",
            {
                "content": [
                    {
                        "text": "[feat/PROJ-4821-registration-tests 3a7f2c1] Add unit tests for registration validation\n 1 file changed, 187 insertions(+)"
                    }
                ]
            },
        ),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_12",
                "input": {"command": "git push origin feat/PROJ-4821-registration-tests"},
            },
        ),
        (
            "tool_result",
            {
                "content": [
                    {
                        "text": "remote: Create a pull request for 'feat/PROJ-4821-registration-tests' on GitHub"
                    }
                ]
            },
        ),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_13",
                "input": {
                    "command": "gh pr create --title 'Add registration validation tests' --body '## Summary\n- 18 unit tests for RegistrationService'"
                },
            },
        ),
        (
            "tool_result",
            {
                "content": [
                    {"text": "https://github.com/org/repo/pull/342\nCI checks: ✓ all passing"}
                ]
            },
        ),
        ("assistant", {"text": "PR #342 created. All CI checks passing. Ready for review."}),
    ],
    "pr_ready": [
        ("thinking", {"thinking": "PR checks passed. I need to add reviewers and post a summary."}),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_14",
                "input": {"command": "gh pr edit 342 --add-reviewer senior-dev,qa-lead"},
            },
        ),
        ("tool_result", {"content": [{"text": "Added reviewers: @senior-dev, @qa-lead"}]}),
        ("assistant", {"text": "Reviewers assigned. Posting coverage summary as PR comment."}),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_15",
                "input": {
                    "command": "gh pr comment 342 --body 'Coverage: 43% → 89% (+46%). 18 new tests added.'"
                },
            },
        ),
        ("tool_result", {"content": [{"text": "Comment posted on PR #342"}]}),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_16",
                "input": {
                    "command": "curl -s 'https://jira.internal/api/issue/PROJ-4821/transition' -d '{\"status\": \"In Review\"}'"
                },
            },
        ),
        ("tool_result", {"content": [{"text": "OK — PROJ-4821 moved to 'In Review'"}]}),
        (
            "assistant",
            {"text": "PR #342 is ready. Reviewers notified. Jira ticket moved to 'In Review'."},
        ),
    ],
    "approve_and_merge": [
        (
            "thinking",
            {"thinking": "The PR has been approved. Let me verify CI is still green and merge."},
        ),
        ("tool_use", {"name": "Bash", "id": "tu_17", "input": {"command": "gh pr checks 342"}}),
        (
            "tool_result",
            {
                "content": [
                    {"text": "✓ CI / test  (pass)\n✓ CI / lint  (pass)\n✓ CI / typecheck  (pass)"}
                ]
            },
        ),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_18",
                "input": {"command": "gh pr merge 342 --squash --delete-branch"},
            },
        ),
        (
            "tool_result",
            {
                "content": [
                    {
                        "text": "✓ Squashed and merged PR #342\n✓ Deleted branch feat/PROJ-4821-registration-tests"
                    }
                ]
            },
        ),
        ("assistant", {"text": "PR #342 squash-merged to main. Feature branch deleted."}),
        (
            "tool_use",
            {
                "name": "Bash",
                "id": "tu_19",
                "input": {
                    "command": "curl -s 'https://jira.internal/api/issue/PROJ-4821/transition' -d '{\"status\": \"Done\"}'"
                },
            },
        ),
        ("tool_result", {"content": [{"text": "OK — PROJ-4821 moved to 'Done'"}]}),
        (
            "assistant",
            {
                "text": "Jira ticket PROJ-4821 closed. Deployment pipeline will pick up the merge automatically."
            },
        ),
    ],
    "ticket_closed": [
        (
            "thinking",
            {"thinking": "Let me write the final summary of everything that was accomplished."},
        ),
        ("assistant", {"text": "Writing final summary for ticket PROJ-4821..."}),
        (
            "tool_use",
            {
                "name": "Write",
                "id": "tu_20",
                "input": {
                    "file_path": "SUMMARY.md",
                    "content": "# PROJ-4821 — Registration Validation Tests\n\n## Outcome\n- 18 unit tests generated\n- Coverage: 43% → 89%\n- PR #342 merged to main\n- No defects found",
                },
            },
        ),
        (
            "assistant",
            {
                "text": "Ticket PROJ-4821 completed. 18 unit tests, +46% coverage, merged via PR #342."
            },
        ),
    ],
}


def _make_stream_event(log_type: str, data: dict) -> StreamEvent:
    if log_type == "thinking":
        content = {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": data["thinking"]}]},
        }
        return StreamEvent(type=StreamEventType.ASSISTANT, content=content, raw=json.dumps(content))
    if log_type == "assistant":
        content = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": data["text"]}]},
        }
        return StreamEvent(type=StreamEventType.ASSISTANT, content=content, raw=json.dumps(content))
    if log_type == "tool_use":
        content = {
            "type": "tool_use",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": data["name"],
                        "id": data["id"],
                        "input": data["input"],
                    }
                ]
            },
        }
        return StreamEvent(type=StreamEventType.TOOL_USE, content=content, raw=json.dumps(content))
    if log_type == "tool_result":
        content = {"type": "tool_result", "message": {"content": data["content"]}}
        return StreamEvent(
            type=StreamEventType.TOOL_RESULT, content=content, raw=json.dumps(content)
        )
    content = {"type": "assistant", "message": {"content": [{"type": "text", "text": str(data)}]}}
    return StreamEvent(type=StreamEventType.ASSISTANT, content=content, raw=json.dumps(content))


class DemoMockSubprocessManager(SubprocessManager):
    def __init__(self, decisions: dict[str, str]) -> None:
        super().__init__()
        self._decisions = decisions

    async def run_task(
        self, prompt: str, workspace: str, session_id: str, *, skip_permissions: bool = False
    ) -> AsyncGenerator[StreamEvent, None]:
        import asyncio

        node_name = self._extract_node_name(prompt)
        task_dir = self._extract_task_dir(prompt)
        logs = NODE_LOGS.get(node_name, [("assistant", {"text": f"Processing {node_name}..."})])
        delay_per_entry = 9.0 / max(len(logs), 1)

        for log_type, data in logs:
            await asyncio.sleep(delay_per_entry * random.uniform(0.5, 1.5))
            yield _make_stream_event(log_type, data)

        if task_dir:
            td = Path(task_dir)
            td.mkdir(parents=True, exist_ok=True)
            if node_name in self._decisions:
                (td / "DECISION.json").write_text(
                    json.dumps(
                        {
                            "decision": self._decisions[node_name],
                            "reasoning": f"Mock routing for {node_name}",
                            "confidence": 0.95,
                        }
                    )
                )
            (td / "SUMMARY.md").write_text(f"# {node_name}\n\nCompleted successfully.")

        await asyncio.sleep(0.5)
        yield StreamEvent(
            type=StreamEventType.RESULT,
            content={"type": "result", "result": "Done.", "duration_ms": 10000, "cost_usd": 0.05},
            raw=json.dumps({"type": "result", "result": "Done."}),
        )
        yield StreamEvent(
            type=StreamEventType.SYSTEM,
            content={"event": "process_exit", "exit_code": 0, "stderr": ""},
            raw="Process exited with code 0",
        )

    async def run_task_resume(
        self, prompt: str, workspace: str, resume_session_id: str, *, skip_permissions: bool = False
    ) -> AsyncGenerator[StreamEvent, None]:
        async for event in self.run_task(prompt, workspace, resume_session_id):
            yield event

    async def run_judge(self, prompt: str, workspace: str, *, skip_permissions: bool = False):
        pass

    async def kill(self, session_id: str) -> None:
        pass

    @staticmethod
    def _extract_node_name(prompt: str) -> str:
        for line in prompt.splitlines():
            s = line.strip()
            if s.startswith("[flowstate:node=") and s.endswith("]"):
                return s[len("[flowstate:node=") : -1]
        return "unknown"

    @staticmethod
    def _extract_task_dir(prompt: str) -> str | None:
        m = re.search(r"Write coordination files to (.+)/\.\s*$", prompt, re.MULTILINE)
        if m:
            return m.group(1)
        m = re.search(r"SUMMARY\.md to (.+)/SUMMARY\.md", prompt)
        if m:
            return m.group(1)
        return None


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/flows", timeout=1)
            if r.status_code in (200, 404):
                return
        except httpx.RequestError:
            pass
        time.sleep(0.2)
    raise RuntimeError("Server did not start")


def get_running_node(base_url: str) -> str | None:
    try:
        runs = httpx.get(f"{base_url}/api/runs", timeout=2).json()
        for r in runs:
            if r.get("status") != "running":
                continue
            detail = httpx.get(f"{base_url}/api/runs/{r['id']}", timeout=2).json()
            for t in detail.get("tasks", []):
                if t.get("status") == "running":
                    return t.get("node_name")
    except Exception:
        pass
    return None


def get_run_status(base_url: str) -> str | None:
    try:
        runs = httpx.get(f"{base_url}/api/runs", timeout=2).json()
        return runs[0].get("status") if runs else None
    except Exception:
        return None


def main() -> None:
    decisions = {"analyze_code": "generate_tests", "open_pr": "pr_ready"}
    mock = DemoMockSubprocessManager(decisions)
    port = find_free_port()

    import tempfile

    tmp = tempfile.mkdtemp()
    watch_dir = Path(tmp) / "flows"
    watch_dir.mkdir()
    (watch_dir / "unit_test_gen.flow").write_text(FLOW_FILE.read_text())

    config = FlowstateConfig(
        server_port=port,
        watch_dir=str(watch_dir),
    )
    app = create_app(config=config, subprocess_manager=mock, static_dir=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    wait_for_server(port)
    base_url = f"http://localhost:{port}"

    print("Waiting for flow discovery...")
    for _ in range(50):
        r = httpx.get(f"{base_url}/api/flows", timeout=2)
        if r.status_code == 200 and any(f.get("name") == "unit_test_gen" for f in r.json()):
            break
        time.sleep(0.3)
    print("Flow discovered!")

    # Clean old outputs
    for f in VIDEO_DIR.glob("*.webm"):
        f.unlink()
    for f in VIDEO_DIR.glob("*.png"):
        f.unlink()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.goto(base_url)
        page.wait_for_load_state("networkidle")
        time.sleep(1)

        # ---- Step 1: Select the flow ----
        print("Selecting flow...")
        flow_entry = page.locator('[data-testid="sidebar-flow-unit_test_gen"]')
        flow_entry.wait_for(state="visible", timeout=10000)
        flow_entry.click()
        time.sleep(2)

        # ---- Step 2: Open the DSL source popup and slowly scroll through it ----
        print("Opening source code popup...")
        source_btn = page.locator("text=View Source")
        if source_btn.count() > 0:
            source_btn.first.click()
            time.sleep(2)

            code_pre = page.locator("pre.source-modal-code")
            code_pre.wait_for(state="visible", timeout=5000)

            scroll_height = code_pre.evaluate("el => el.scrollHeight")
            client_height = code_pre.evaluate("el => el.clientHeight")
            scroll_distance = scroll_height - client_height

            print(f"  Code: {scroll_height}px total, {scroll_distance}px to scroll")

            if scroll_distance > 10:
                steps = 90
                step_px = scroll_distance / steps
                for i in range(steps):
                    code_pre.evaluate(f"el => el.scrollTop = {(i + 1) * step_px}")
                    time.sleep(18.0 / steps)
                time.sleep(2)
            else:
                time.sleep(20)

            page.keyboard.press("Escape")
            time.sleep(1)
        else:
            print("  (no View Source button, showing graph for 20s)")
            time.sleep(20)

        # ---- Step 3: Submit a task ----
        print("Submitting task...")
        submit_btn = page.locator('[data-testid="submit-task-btn"]')
        submit_btn.wait_for(state="visible", timeout=5000)
        submit_btn.click()
        time.sleep(1)

        modal = page.locator(".task-modal-content")
        modal.wait_for(state="visible", timeout=5000)
        modal.locator(".task-modal-input").first.fill(
            "PROJ-4821: Add registration validation tests"
        )
        time.sleep(1)
        modal.locator(".task-modal-btn-submit").click()
        time.sleep(2)

        # ---- Step 4: Open run detail ----
        print("Opening run detail...")
        run_link = page.locator('[data-testid^="sidebar-run-"]').first
        run_link.wait_for(state="visible", timeout=15000)
        run_link.click()
        time.sleep(2)

        # ---- Step 5: Watch execution ----
        print("Watching flow execute...")
        last_node = None
        seen: set[str] = set()

        for _ in range(300):
            running = get_running_node(base_url)

            if running and running != last_node:
                print(f"  → {running}")
                last_node = running
                seen.add(running)

                node_el = page.locator(f'[data-testid="node-{running}"]')
                if node_el.count() > 0:
                    node_el.scroll_into_view_if_needed()
                    time.sleep(0.3)
                    node_el.click()
                    time.sleep(0.5)

            status = get_run_status(base_url)
            if status in ("completed", "failed", "cancelled"):
                print(f"Flow finished: {status}")
                time.sleep(2)

                if last_node:
                    node_el = page.locator(f'[data-testid="node-{last_node}"]')
                    if node_el.count() > 0:
                        node_el.click()
                        time.sleep(3)

                page.reload(wait_until="networkidle")
                time.sleep(2)

                first = page.locator('[data-testid="node-receive_ticket"]')
                if first.count() > 0:
                    first.click()
                    time.sleep(4)
                break

            time.sleep(0.5)

        page.screenshot(path=str(VIDEO_DIR / "unit_test_gen_final.png"))
        time.sleep(2)
        context.close()
        browser.close()

    videos = list(VIDEO_DIR.glob("*.webm"))
    if videos:
        # Rename to a clean name
        final_video = VIDEO_DIR / "unit_test_gen_demo.webm"
        if final_video.exists():
            final_video.unlink()
        videos[0].rename(final_video)
        print(f"\nVideo: {final_video}")

    print(f"Screenshot: {VIDEO_DIR / 'unit_test_gen_final.png'}")
    print(f"Nodes: {', '.join(seen)}")
    server.should_exit = True
    print("Done!")


if __name__ == "__main__":
    main()
