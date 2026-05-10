"""Tests for the persist-exit-worktree feature (ENGINE-088).

Covers both the low-level ``merge_to_source_branch_via_detached_worktree``
helper and the executor's ``_persist_exit_worktree`` integration. All tests
run against real ``tmp_path`` git repositories — the helper itself shells
out to ``git`` and must be validated end-to-end.

Sprint-37c TEST IDs in docstrings map to acceptance tests in
``issues/sprints/sprint-037.md``.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from flowstate.engine.events import EventType, FlowEvent
from flowstate.engine.worktree import (
    PersistResult,
    capture_source_branch,
    create_node_worktree,
    merge_to_source_branch_via_detached_worktree,
)

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run a git command in ``repo`` synchronously and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def _git_rc(repo: Path, *args: str) -> int:
    """Run a git command, return its returncode (do not raise on failure)."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
    ).returncode


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a real git repo with a single initial commit on ``main``."""
    repo = tmp_path / "journal"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@flowstate.dev")
    _git(repo, "config", "user.name", "Flowstate Test")
    (repo / "README.md").write_text("# Journal\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _make_exit_branch(repo: Path, branch: str, file: str, content: str) -> str:
    """Create a branch in ``repo`` with one extra commit. Return the commit SHA."""
    _git(repo, "branch", branch)
    _git(repo, "switch", branch)
    (repo / file).write_text(content)
    _git(repo, "add", file)
    _git(repo, "commit", "-m", f"add {file}")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "main")
    return sha


# --------------------------------------------------------------------------
# capture_source_branch
# --------------------------------------------------------------------------


class TestCaptureSourceBranch:
    """``capture_source_branch`` returns the current branch or None."""

    @pytest.mark.asyncio
    async def test_returns_branch_name(self, git_repo: Path) -> None:
        assert await capture_source_branch(str(git_repo)) == "main"

    @pytest.mark.asyncio
    async def test_detached_head_returns_none(self, git_repo: Path) -> None:
        sha = _git(git_repo, "rev-parse", "HEAD")
        _git(git_repo, "checkout", sha)
        assert await capture_source_branch(str(git_repo)) is None

    @pytest.mark.asyncio
    async def test_non_repo_returns_none(self, tmp_path: Path) -> None:
        assert await capture_source_branch(str(tmp_path)) is None


# --------------------------------------------------------------------------
# merge_to_source_branch_via_detached_worktree -- happy path / TEST-37c.8
# --------------------------------------------------------------------------


class TestMergeHappyPath:
    """A successful, conflict-free merge advances the source branch."""

    @pytest.mark.asyncio
    async def test_advances_source_branch(self, git_repo: Path) -> None:
        c0 = _git(git_repo, "rev-parse", "HEAD")
        _make_exit_branch(git_repo, "exit-branch", "feature.txt", "hello\n")

        result = await merge_to_source_branch_via_detached_worktree(
            original_workspace=str(git_repo),
            source_branch="main",
            exit_branch="exit-branch",
        )

        assert result.status == "advanced"
        assert result.old_commit == c0
        assert result.new_commit is not None and result.new_commit != c0
        # The new commit is a merge commit (two parents) ^1=c0, ^2=exit head.
        parents = _git(git_repo, "log", "--pretty=%P", "-n", "1", "main")
        assert len(parents.split()) == 2
        # The feature file is reachable on main.
        feature_sha = _git(git_repo, "rev-parse", "main:feature.txt")
        assert feature_sha  # exists


# --------------------------------------------------------------------------
# TEST-37c.7 + TEST-37c.9: user's working tree is untouched
# --------------------------------------------------------------------------


class TestWorkingTreeUntouched:
    """The detached merge MUST NOT modify the user's main checkout."""

    @pytest.mark.asyncio
    async def test_dirty_working_tree_preserved(self, git_repo: Path) -> None:
        # Create the exit branch WITHOUT touching the user's checkout: build
        # it inside a real flowstate worktree (the production pattern).
        wt_info = await create_node_worktree(str(git_repo), "abcd1234-run", "end", 1)
        (Path(wt_info.worktree_path) / "feature.txt").write_text("hello\n")
        _git(Path(wt_info.worktree_path), "add", "feature.txt")
        _git(Path(wt_info.worktree_path), "commit", "-m", "feature")
        exit_branch = wt_info.branch_name

        # Now dirty the user's main checkout: stage a tracked file and create
        # an untracked file.
        (git_repo / "README.md").write_text("# Journal\nmidedit\n")
        _git(git_repo, "add", "README.md")
        (git_repo / "scratch.md").write_text("scratch notes\n")
        before_status = _git(git_repo, "status", "--porcelain")
        before_head = _git(git_repo, "symbolic-ref", "--short", "HEAD")
        before_staged = _git(git_repo, "diff", "--staged")

        result = await merge_to_source_branch_via_detached_worktree(
            original_workspace=str(git_repo),
            source_branch="main",
            exit_branch=exit_branch,
        )
        assert result.status == "advanced"

        # Working tree shape must be unchanged (still on main, still dirty).
        assert _git(git_repo, "symbolic-ref", "--short", "HEAD") == before_head
        after_status = _git(git_repo, "status", "--porcelain")
        # The untracked + staged entries should still show up. main advanced,
        # so the staged file's diff vs HEAD may now show README.md as
        # modified-vs-HEAD (because HEAD moved). Verify the user's own
        # staged content still exists in the index.
        assert "scratch.md" in after_status
        # Index still contains the user's staged blob.
        after_staged = _git(git_repo, "diff", "--staged")
        # `git diff --staged` is index-vs-HEAD; HEAD moved, but our staged
        # blob is still there. The simplest invariant: the README in the
        # *index* still has the "midedit" line.
        index_readme = subprocess.run(
            ["git", "show", ":README.md"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "midedit" in index_readme
        # And the untracked file is still untouched.
        assert (git_repo / "scratch.md").read_text() == "scratch notes\n"
        # Sanity: at least one of these is non-empty so we know we measured.
        assert before_status or after_status or before_staged or after_staged


# --------------------------------------------------------------------------
# TEST-37c.10: real merge conflict preserves exit branch
# --------------------------------------------------------------------------


class TestMergeConflict:
    """Conflicts must NOT advance the source branch; helper signals 'conflict'."""

    @pytest.mark.asyncio
    async def test_conflict_preserves_main(self, git_repo: Path) -> None:
        # Set up a file that both branches modify on the same line.
        (git_repo / "file.txt").write_text("original\n")
        _git(git_repo, "add", "file.txt")
        _git(git_repo, "commit", "-m", "add file")
        c0 = _git(git_repo, "rev-parse", "HEAD")

        # Exit branch modifies the line.
        _git(git_repo, "branch", "exit-branch")
        _git(git_repo, "switch", "exit-branch")
        (git_repo / "file.txt").write_text("from exit\n")
        _git(git_repo, "add", "file.txt")
        _git(git_repo, "commit", "-m", "exit edit")
        _git(git_repo, "switch", "main")

        # Main also modifies the same line.
        (git_repo / "file.txt").write_text("from main\n")
        _git(git_repo, "add", "file.txt")
        _git(git_repo, "commit", "-m", "main edit")
        c1 = _git(git_repo, "rev-parse", "HEAD")
        assert c1 != c0

        result = await merge_to_source_branch_via_detached_worktree(
            original_workspace=str(git_repo),
            source_branch="main",
            exit_branch="exit-branch",
        )

        assert result.status == "conflict"
        assert "file.txt" in result.conflict_files
        # Main is unchanged.
        assert _git(git_repo, "rev-parse", "main") == c1
        # The exit branch still exists.
        assert _git_rc(git_repo, "rev-parse", "--verify", "refs/heads/exit-branch") == 0


# --------------------------------------------------------------------------
# TEST-37c.11: CAS retry succeeds via deterministic pre_cas_hook
# --------------------------------------------------------------------------


class TestCasRetry:
    """When source branch moves between rev-parse and update-ref, retry."""

    @pytest.mark.asyncio
    async def test_cas_retry_succeeds_on_second_attempt(self, git_repo: Path) -> None:
        c0 = _git(git_repo, "rev-parse", "HEAD")

        # Create the exit branch (modifies a separate file -- no conflict).
        _make_exit_branch(git_repo, "exit-branch", "exit.txt", "from exit\n")

        # Pre-stage a competing commit OBJECT we can fast-forward main to on
        # the first CAS attempt. Make it on a side branch first so the
        # commit object exists but main hasn't moved yet.
        _git(git_repo, "switch", "-c", "competitor")
        (git_repo / "competitor.txt").write_text("from competitor\n")
        _git(git_repo, "add", "competitor.txt")
        _git(git_repo, "commit", "-m", "competitor")
        competitor_sha = _git(git_repo, "rev-parse", "HEAD")
        _git(git_repo, "switch", "main")
        # Sanity: main has NOT advanced yet.
        assert _git(git_repo, "rev-parse", "main") == c0

        calls: list[int] = []

        async def hook(attempt: int) -> None:
            calls.append(attempt)
            if attempt == 0:
                # Move main to competitor commit -- CAS will fail on
                # this attempt because old_commit captured c0.
                _git(git_repo, "update-ref", "refs/heads/main", competitor_sha)

        result = await merge_to_source_branch_via_detached_worktree(
            original_workspace=str(git_repo),
            source_branch="main",
            exit_branch="exit-branch",
            max_cas_retries=3,
            pre_cas_hook=hook,
        )

        assert result.status == "advanced"
        assert calls == [0, 1]
        # Final main is a merge whose ^1 is the competitor sha.
        first_parent = _git(git_repo, "rev-parse", "main^1")
        assert first_parent == competitor_sha


# --------------------------------------------------------------------------
# TEST-37c.12: CAS exhaustion path
# --------------------------------------------------------------------------


class TestCasExhausted:
    """When CAS keeps failing, return 'cas_exhausted'."""

    @pytest.mark.asyncio
    async def test_cas_exhausted_after_three_attempts(self, git_repo: Path) -> None:
        c0 = _git(git_repo, "rev-parse", "HEAD")
        _make_exit_branch(git_repo, "exit-branch", "exit.txt", "hi\n")

        # Pre-build a chain of side-branch commits we can advance main to
        # on each attempt.
        side_shas: list[str] = []
        _git(git_repo, "switch", "-c", "side")
        for i in range(5):
            (git_repo / f"s{i}.txt").write_text(f"{i}\n")
            _git(git_repo, "add", f"s{i}.txt")
            _git(git_repo, "commit", "-m", f"side {i}")
            side_shas.append(_git(git_repo, "rev-parse", "HEAD"))
        _git(git_repo, "switch", "main")

        # Reset main to c0 (the chain is on `side`).
        _git(git_repo, "update-ref", "refs/heads/main", c0)

        async def hook(attempt: int) -> None:
            # Move main forward every attempt so CAS always fails.
            _git(git_repo, "update-ref", "refs/heads/main", side_shas[attempt])

        result = await merge_to_source_branch_via_detached_worktree(
            original_workspace=str(git_repo),
            source_branch="main",
            exit_branch="exit-branch",
            max_cas_retries=3,
            pre_cas_hook=hook,
        )

        assert result.status == "cas_exhausted"
        # Main ended up at the third side commit (attempt index 2), NOT at
        # a flowstate merge commit.
        assert _git(git_repo, "rev-parse", "main") == side_shas[2]


# --------------------------------------------------------------------------
# TEST-37c.13: file lock serializes concurrent runs
# --------------------------------------------------------------------------


class TestConcurrentSerializes:
    """Two concurrent persists for the same workspace must serialize."""

    @pytest.mark.asyncio
    async def test_concurrent_runs_serialize(self, git_repo: Path) -> None:
        c0 = _git(git_repo, "rev-parse", "HEAD")
        _make_exit_branch(git_repo, "exit-a", "a.txt", "from a\n")
        _make_exit_branch(git_repo, "exit-b", "b.txt", "from b\n")

        task_a = asyncio.create_task(
            merge_to_source_branch_via_detached_worktree(
                original_workspace=str(git_repo),
                source_branch="main",
                exit_branch="exit-a",
            )
        )
        task_b = asyncio.create_task(
            merge_to_source_branch_via_detached_worktree(
                original_workspace=str(git_repo),
                source_branch="main",
                exit_branch="exit-b",
            )
        )

        results = await asyncio.gather(task_a, task_b)
        # Both succeed (lock serializes; CAS retry covers any race in case
        # the lock is held by a non-flowstate writer).
        assert all(r.status == "advanced" for r in results)
        # Main is now ahead of c0 with both feature files present.
        assert _git(git_repo, "rev-parse", "main") != c0
        assert (git_repo / "a.txt").exists() is False  # not in working tree
        # But reachable from main.
        assert _git(git_repo, "rev-parse", "main:a.txt")
        assert _git(git_repo, "rev-parse", "main:b.txt")


# --------------------------------------------------------------------------
# Defensive skip cases on the helper
# --------------------------------------------------------------------------


class TestHelperSkipCases:
    """The helper short-circuits cleanly on missing branches."""

    @pytest.mark.asyncio
    async def test_skips_when_exit_branch_missing(self, git_repo: Path) -> None:
        result = await merge_to_source_branch_via_detached_worktree(
            original_workspace=str(git_repo),
            source_branch="main",
            exit_branch="nope/not/a/branch",
        )
        assert result.status == "skipped"
        assert result.reason == "exit_branch_missing"

    @pytest.mark.asyncio
    async def test_skips_when_source_branch_missing(self, git_repo: Path) -> None:
        _make_exit_branch(git_repo, "exit-branch", "f.txt", "x\n")
        result = await merge_to_source_branch_via_detached_worktree(
            original_workspace=str(git_repo),
            source_branch="does-not-exist",
            exit_branch="exit-branch",
        )
        assert result.status == "skipped"
        assert result.reason == "source_branch_missing"


# --------------------------------------------------------------------------
# Executor integration: _persist_exit_worktree skip cases (TEST-37c.15)
# --------------------------------------------------------------------------


def _make_executor() -> tuple[object, list[FlowEvent]]:
    """Build a FlowExecutor with in-memory DB and a no-op harness.

    Returns the executor and a list-of-events captured via the callback.
    """
    from flowstate.engine.executor import FlowExecutor
    from flowstate.state.repository import FlowstateDB

    repo = FlowstateDB(":memory:")
    events: list[FlowEvent] = []

    class _NoopHarness:
        """Bare-minimum harness; never invoked in skip-case tests."""

        async def run_task(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("harness should not be invoked in skip-case tests")

    executor = FlowExecutor(
        db=repo,
        event_callback=events.append,
        harness=_NoopHarness(),  # type: ignore[arg-type]
        server_base_url="http://localhost:9999",
    )
    return executor, events


def _seed_flow_run(repo: object, *, run_id: str = "run-test") -> str:
    """Insert a minimal flow_definition + flow_run row for skip-case tests."""
    repo_any: object = repo
    flow_def_id = repo_any.create_flow_definition(  # type: ignore[attr-defined]
        name="test_flow",
        source_dsl="",
        ast_json="{}",
    )
    return repo_any.create_flow_run(  # type: ignore[attr-defined]
        flow_definition_id=flow_def_id,
        data_dir="",
        budget_seconds=60,
        on_error="pause",
        default_workspace="/tmp/x",
        params_json=None,
        run_id=run_id,
    )


def _minimal_flow() -> object:
    """Build a minimal Flow object with worktree_persist=True."""
    from flowstate.dsl.ast import (
        ContextMode,
        ErrorPolicy,
        Flow,
        Node,
        NodeType,
        OverlapPolicy,
    )

    entry = Node(
        name="start",
        node_type=NodeType.ENTRY,
        prompt="",
    )
    exit_node = Node(
        name="end",
        node_type=NodeType.EXIT,
        prompt="",
    )
    return Flow(
        name="test_flow",
        budget_seconds=60,
        on_error=ErrorPolicy.PAUSE,
        context=ContextMode.HANDOFF,
        on_overlap=OverlapPolicy.SKIP,
        worktree=True,
        worktree_persist=True,
        nodes={"start": entry, "end": exit_node},
    )


@pytest.mark.asyncio
async def test_persist_skips_when_no_source_branch_recorded() -> None:
    """TEST-37c.15(a): source_branch NULL on run -> skipped."""
    executor, _events = _make_executor()
    run_id = _seed_flow_run(executor._db)  # type: ignore[attr-defined]
    flow = _minimal_flow()
    result = await executor._persist_exit_worktree(run_id, flow)  # type: ignore[attr-defined]
    assert isinstance(result, PersistResult)
    assert result.status == "skipped"
    assert result.reason == "no_source_branch"


@pytest.mark.asyncio
async def test_persist_skips_when_no_exit_task() -> None:
    """TEST-37c.15(b): no completed exit task -> skipped."""
    executor, _events = _make_executor()
    run_id = _seed_flow_run(executor._db)  # type: ignore[attr-defined]
    executor._db.set_source_branch(run_id, "main")  # type: ignore[attr-defined]
    flow = _minimal_flow()
    result = await executor._persist_exit_worktree(run_id, flow)  # type: ignore[attr-defined]
    assert result.status == "skipped"
    assert result.reason == "no_exit_task"


@pytest.mark.asyncio
async def test_persist_skips_when_exit_task_has_no_worktree_artifact() -> None:
    """TEST-37c.15(d): exit task without worktree artifact -> skipped."""
    executor, _events = _make_executor()
    repo_any: object = executor._db  # type: ignore[attr-defined]
    run_id = _seed_flow_run(repo_any)
    repo_any.set_source_branch(run_id, "main")  # type: ignore[attr-defined]

    # Insert a completed exit task with no worktree artifact.
    exit_id = repo_any.create_task_execution(  # type: ignore[attr-defined]
        flow_run_id=run_id,
        node_name="end",
        node_type="exit",
        generation=1,
        context_mode="handoff",
        cwd="/tmp/x",
        task_dir="",
        prompt_text="",
    )
    repo_any.update_task_status(exit_id, "completed")  # type: ignore[attr-defined]

    flow = _minimal_flow()
    result = await executor._persist_exit_worktree(run_id, flow)  # type: ignore[attr-defined]
    assert result.status == "skipped"
    assert result.reason == "no_worktree_artifact"


@pytest.mark.asyncio
async def test_persist_skips_when_exit_task_reached_via_context_none() -> None:
    """TEST-37c.15(c): exit reached via context=none -> skipped."""
    executor, _events = _make_executor()
    repo_any: object = executor._db  # type: ignore[attr-defined]
    run_id = _seed_flow_run(repo_any)
    repo_any.set_source_branch(run_id, "main")  # type: ignore[attr-defined]

    exit_id = repo_any.create_task_execution(  # type: ignore[attr-defined]
        flow_run_id=run_id,
        node_name="end",
        node_type="exit",
        generation=1,
        context_mode="none",
        cwd="/tmp/x",
        task_dir="",
        prompt_text="",
    )
    repo_any.update_task_status(exit_id, "completed")  # type: ignore[attr-defined]

    flow = _minimal_flow()
    result = await executor._persist_exit_worktree(run_id, flow)  # type: ignore[attr-defined]
    assert result.status == "skipped"
    assert result.reason == "none_context_exit"


# --------------------------------------------------------------------------
# Executor integration: full happy path via real git repo
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_advances_source_branch_via_executor(git_repo: Path) -> None:
    """End-to-end persist through the executor's _persist_exit_worktree.

    Wires real artifacts -- a real worktree on a real branch -- and
    verifies the SOURCE_BRANCH_ADVANCED event fires with old/new commits.
    """
    from flowstate.engine.worktree import worktree_artifact_to_json

    executor, events = _make_executor()
    repo_any: object = executor._db  # type: ignore[attr-defined]
    run_id = _seed_flow_run(repo_any)
    repo_any.set_source_branch(run_id, "main")  # type: ignore[attr-defined]

    # Create a real worktree with an extra commit so the merge has work.
    c0 = _git(git_repo, "rev-parse", "HEAD")
    info = await create_node_worktree(str(git_repo), "abcd1234-run", "end", 1)
    (Path(info.worktree_path) / "feature.txt").write_text("hello\n")
    _git(Path(info.worktree_path), "add", "feature.txt")
    _git(Path(info.worktree_path), "commit", "-m", "feature")

    # Seed a completed exit task with the worktree artifact.
    exit_id = repo_any.create_task_execution(  # type: ignore[attr-defined]
        flow_run_id=run_id,
        node_name="end",
        node_type="exit",
        generation=1,
        context_mode="handoff",
        cwd=info.worktree_path,
        task_dir="",
        prompt_text="",
    )
    repo_any.update_task_status(exit_id, "completed")  # type: ignore[attr-defined]
    repo_any.save_artifact(  # type: ignore[attr-defined]
        exit_id, "worktree", worktree_artifact_to_json(info), "application/json"
    )

    flow = _minimal_flow()
    result = await executor._persist_exit_worktree(run_id, flow)  # type: ignore[attr-defined]
    assert result.status == "advanced"
    assert result.old_commit == c0
    assert result.new_commit and result.new_commit != c0

    # The branch was advanced AND no preserved branches were recorded.
    assert _git(git_repo, "rev-parse", "main") == result.new_commit
    assert executor._preserved_branches == set()  # type: ignore[attr-defined]

    # The SOURCE_BRANCH_ADVANCED event fired with the expected payload.
    advanced_events = [e for e in events if e.type == EventType.SOURCE_BRANCH_ADVANCED]
    assert len(advanced_events) == 1
    payload = advanced_events[0].payload
    assert payload["source_branch"] == "main"
    assert payload["old_commit"] == c0
    assert payload["new_commit"] == result.new_commit
    assert payload["exit_branch"] == info.branch_name


@pytest.mark.asyncio
async def test_persist_conflict_preserves_exit_branch_via_executor(
    git_repo: Path,
) -> None:
    """A real conflict marks the run preserved and emits the conflict event."""
    from flowstate.engine.worktree import worktree_artifact_to_json

    executor, events = _make_executor()
    repo_any: object = executor._db  # type: ignore[attr-defined]
    run_id = _seed_flow_run(repo_any)
    repo_any.set_source_branch(run_id, "main")  # type: ignore[attr-defined]

    # Set up a file both branches will touch on the same line.
    (git_repo / "file.txt").write_text("v0\n")
    _git(git_repo, "add", "file.txt")
    _git(git_repo, "commit", "-m", "add file")

    # Create the worktree and have it modify file.txt.
    info = await create_node_worktree(str(git_repo), "abcd1234-run", "end", 1)
    (Path(info.worktree_path) / "file.txt").write_text("from worktree\n")
    _git(Path(info.worktree_path), "add", "file.txt")
    _git(Path(info.worktree_path), "commit", "-m", "wt edit")

    # Main also moves on file.txt (simulating a concurrent user commit).
    (git_repo / "file.txt").write_text("from main\n")
    _git(git_repo, "add", "file.txt")
    _git(git_repo, "commit", "-m", "main edit")
    main_sha = _git(git_repo, "rev-parse", "main")

    # Seed exit task + artifact.
    exit_id = repo_any.create_task_execution(  # type: ignore[attr-defined]
        flow_run_id=run_id,
        node_name="end",
        node_type="exit",
        generation=1,
        context_mode="handoff",
        cwd=info.worktree_path,
        task_dir="",
        prompt_text="",
    )
    repo_any.update_task_status(exit_id, "completed")  # type: ignore[attr-defined]
    repo_any.save_artifact(  # type: ignore[attr-defined]
        exit_id, "worktree", worktree_artifact_to_json(info), "application/json"
    )

    flow = _minimal_flow()
    result = await executor._persist_exit_worktree(run_id, flow)  # type: ignore[attr-defined]
    assert result.status == "conflict"
    assert "file.txt" in result.conflict_files
    # Main is NOT advanced.
    assert _git(git_repo, "rev-parse", "main") == main_sha
    # Branch is preserved for cleanup-skip.
    assert info.branch_name in executor._preserved_branches  # type: ignore[attr-defined]

    conflict_events = [e for e in events if e.type == EventType.SOURCE_BRANCH_PERSIST_CONFLICT]
    assert len(conflict_events) == 1
    payload = conflict_events[0].payload
    assert payload["source_branch"] == "main"
    assert payload["preserved_branch"] == info.branch_name
    assert "file.txt" in payload["conflict_files"]  # type: ignore[operator]
