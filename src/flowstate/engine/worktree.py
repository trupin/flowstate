"""Git worktree management for workspace isolation.

Provides per-node worktree isolation: each node in a flow gets its own git
worktree. Worktree references flow along edges:
  - Linear/conditional: next node inherits predecessor's worktree (reuse)
  - Fork (1->N): each branch gets a new worktree branched from predecessor's HEAD
  - Join (N->1): join node merges all branch worktrees before starting

The worktree reference is stored as a ``worktree`` artifact on each task
execution (path + branch name + original workspace).
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


# Type alias for the optional pre-CAS deterministic injection hook used by
# tests to mutate the source branch between rev-parse and update-ref. Always
# None in production code paths.
PreCasHook = Callable[[int], Awaitable[None]]
"""Async callable invoked just before each CAS attempt.

Argument is the zero-based attempt number. Used by tests (TEST-37c.11) to
deterministically mutate the source branch between rev-parse and
update-ref so the CAS retry path is exercised without flaky timing.
"""


class WorktreeError(Exception):
    """Raised when worktree creation or cleanup fails."""


@dataclass
class WorktreeInfo:
    """Holds the worktree state for a flow run."""

    original_workspace: str  # Absolute path to the original git repo
    worktree_path: str  # Absolute path to the created worktree
    branch_name: str  # Branch name (flowstate/<run-id-prefix>)


async def init_git_repo(path: str) -> bool:
    """Initialize a git repo with an initial empty commit.

    Used to bootstrap auto-created workspaces so that worktree isolation
    has a HEAD to branch from.

    Returns True if successful, False if git is not available or the
    commands fail.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "init",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            return False

        # Initial commit so worktree creation has a HEAD to branch from
        proc = await asyncio.create_subprocess_exec(
            "git",
            "commit",
            "--allow-empty",
            "-m",
            "flowstate: init workspace",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def is_git_repo(path: str) -> bool:
    """Check if path is a git repository (has .git dir or .git file)."""
    git_path = Path(path) / ".git"
    return git_path.exists()


def is_existing_worktree(path: str) -> bool:
    """Check if path is already a git worktree (to avoid nesting).

    A worktree has a .git FILE (not directory) pointing to the main repo's
    .git/worktrees/<name> directory.
    """
    git_path = Path(path) / ".git"
    return git_path.is_file()


async def create_worktree(workspace: str, run_id: str) -> WorktreeInfo:
    """Create a git worktree for a flow run.

    Creates a new worktree branched from HEAD:
    - Branch: flowstate/<run_id[:8]>
    - Path: /tmp/flowstate-<run_id[:8]>-<random>/

    Raises WorktreeError if creation fails.
    """
    workspace = str(Path(workspace).resolve())
    branch_name = f"flowstate/{run_id[:8]}"
    worktree_dir = tempfile.mkdtemp(prefix=f"flowstate-{run_id[:8]}-")

    proc = await asyncio.create_subprocess_exec(
        "git",
        "worktree",
        "add",
        worktree_dir,
        "-b",
        branch_name,
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        # Try with longer UUID suffix if branch already exists
        branch_name = f"flowstate/{run_id[:12]}"
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "add",
            worktree_dir,
            "-b",
            branch_name,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            # Clean up the empty temp dir
            shutil.rmtree(worktree_dir, ignore_errors=True)
            raise WorktreeError(f"Failed to create worktree: {stderr.decode().strip()}")

    return WorktreeInfo(
        original_workspace=workspace,
        worktree_path=worktree_dir,
        branch_name=branch_name,
    )


async def cleanup_worktree(info: WorktreeInfo) -> None:
    """Remove a git worktree and delete its branch.

    Runs:
    1. git worktree remove <path> --force
    2. git branch -D <branch>

    Logs warnings on failure but does not raise.
    """
    # Remove worktree
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "worktree",
            "remove",
            info.worktree_path,
            "--force",
            cwd=info.original_workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            logger.warning("Failed to remove worktree at %s", info.worktree_path)
    except Exception:
        logger.warning("Error removing worktree at %s", info.worktree_path, exc_info=True)

    # Delete branch
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "-D",
            info.branch_name,
            cwd=info.original_workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            logger.warning("Failed to delete branch %s", info.branch_name)
    except Exception:
        logger.warning("Error deleting branch %s", info.branch_name, exc_info=True)


@dataclass
class MergeResult:
    """Result of merging multiple worktree branches."""

    has_conflicts: bool
    conflict_files: list[str] = field(default_factory=list)


async def create_node_worktree(
    workspace: str,
    run_id: str,
    node_name: str,
    generation: int,
    source_branch: str | None = None,
) -> WorktreeInfo:
    """Create a worktree for a specific node execution.

    Branch name: ``flowstate/<run_id[:8]>/<node_name>-<generation>``
    If *source_branch* is provided, branch from it instead of HEAD.

    Raises WorktreeError if creation fails.
    """
    workspace = str(Path(workspace).resolve())
    branch_name = f"flowstate/{run_id[:8]}/{node_name}-{generation}"
    worktree_dir = tempfile.mkdtemp(prefix=f"flowstate-{run_id[:8]}-{node_name}-")

    cmd: list[str] = ["git", "worktree", "add", worktree_dir, "-b", branch_name]
    if source_branch:
        cmd.append(source_branch)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        # Try with longer UUID suffix if branch already exists
        branch_name = f"flowstate/{run_id[:12]}/{node_name}-{generation}"
        cmd = ["git", "worktree", "add", worktree_dir, "-b", branch_name]
        if source_branch:
            cmd.append(source_branch)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            shutil.rmtree(worktree_dir, ignore_errors=True)
            raise WorktreeError(f"Failed to create node worktree: {stderr.decode().strip()}")

    return WorktreeInfo(
        original_workspace=workspace,
        worktree_path=worktree_dir,
        branch_name=branch_name,
    )


async def merge_worktrees(
    target: WorktreeInfo,
    source_branches: list[str],
) -> MergeResult:
    """Merge source branches into the target worktree.

    Uses sequential ``git merge`` for each source branch. Successful
    merges auto-commit. If conflicts occur on any branch, the conflicts
    are recorded, then resolved by adding all files and committing so
    that subsequent branch merges can proceed. The final working tree
    will contain conflict markers for the agent to resolve.

    Returns a MergeResult indicating whether any conflicts were found.
    """
    all_conflict_files: list[str] = []
    has_conflicts = False

    for branch in source_branches:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "merge",
            "--no-ff",
            "-m",
            f"flowstate: merge {branch}",
            branch,
            cwd=target.worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            # Clean merge, auto-committed
            continue

        # Check for merge conflicts (exit code 1 with conflicts)
        conflict_proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--name-only",
            "--diff-filter=U",
            cwd=target.worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        conflict_stdout, _ = await conflict_proc.communicate()
        conflict_files = [f for f in conflict_stdout.decode().strip().split("\n") if f]
        if conflict_files:
            has_conflicts = True
            all_conflict_files.extend(conflict_files)
            logger.warning(
                "Merge conflicts in %d files from branch %s",
                len(conflict_files),
                branch,
            )
            # Add all files (including conflicted ones) and commit so
            # subsequent merges can proceed. The conflict markers
            # remain in the file content for the agent to resolve.
            await asyncio.create_subprocess_exec(
                "git",
                "add",
                "--all",
                cwd=target.worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            commit_proc = await asyncio.create_subprocess_exec(
                "git",
                "commit",
                "-m",
                f"flowstate: merge {branch} (with conflicts)",
                cwd=target.worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await commit_proc.communicate()
        else:
            logger.warning(
                "Merge failed (non-conflict) for branch %s: %s",
                branch,
                stderr.decode().strip(),
            )

    return MergeResult(has_conflicts=has_conflicts, conflict_files=all_conflict_files)


def worktree_to_dict(info: WorktreeInfo) -> dict[str, str]:
    """Serialize a WorktreeInfo to a dict for artifact storage."""
    return {
        "path": info.worktree_path,
        "branch": info.branch_name,
        "original_workspace": info.original_workspace,
    }


def worktree_from_dict(data: dict[str, Any]) -> WorktreeInfo:
    """Deserialize a WorktreeInfo from an artifact dict."""
    return WorktreeInfo(
        original_workspace=data["original_workspace"],
        worktree_path=data["path"],
        branch_name=data["branch"],
    )


def worktree_artifact_to_json(info: WorktreeInfo) -> str:
    """Serialize a WorktreeInfo to a JSON string for artifact storage."""
    return json.dumps(worktree_to_dict(info))


def worktree_artifact_from_json(content: str) -> WorktreeInfo:
    """Deserialize a WorktreeInfo from an artifact JSON string."""
    return worktree_from_dict(json.loads(content))


def map_cwd_to_worktree(
    logical_cwd: str,
    original_workspace: str,
    worktree_path: str,
) -> str:
    """Remap a logical cwd through the worktree.

    If logical_cwd starts with original_workspace (or equals it),
    replace the prefix with worktree_path.
    Otherwise return logical_cwd unchanged.

    All paths are resolved to absolute before comparison.
    """
    resolved_cwd = str(Path(logical_cwd).resolve())
    resolved_workspace = str(Path(original_workspace).resolve())
    resolved_worktree = str(Path(worktree_path).resolve())

    if resolved_cwd == resolved_workspace:
        return resolved_worktree

    # Check if cwd is under workspace (e.g., /repo/subdir)
    prefix = resolved_workspace + "/"
    if resolved_cwd.startswith(prefix):
        relative = resolved_cwd[len(prefix) :]
        return str(Path(resolved_worktree) / relative)

    # Not under workspace — return unchanged
    return logical_cwd


# ====================================================================== #
# ENGINE-088: Persist exit worktree to source branch via detached worktree
# ====================================================================== #


PersistStatus = Literal["advanced", "conflict", "skipped", "cas_exhausted"]


@dataclass
class PersistResult:
    """Outcome of attempting to merge an exit worktree into the source branch.

    Attributes:
        status:
            - ``"advanced"``: source branch ref was atomically advanced. The
              merge commit is in ``new_commit``.
            - ``"conflict"``: ``git merge`` had textual conflicts. Source
              branch was NOT advanced. ``conflict_files`` is populated.
            - ``"cas_exhausted"``: ``git update-ref`` CAS failed on every
              attempt because the source branch was being moved by another
              writer. Treated like a conflict for cleanup purposes.
            - ``"skipped"``: short-circuited for a documented benign reason
              (no source branch, no exit branch, etc.). ``reason`` is set.
        old_commit: The source branch commit immediately before the merge.
            Set on every status except some early skips.
        new_commit: The merge commit hash. Set only when ``status ==
            "advanced"``.
        conflict_files: Paths reported by ``git diff --name-only
            --diff-filter=U``. Populated when ``status == "conflict"``.
        reason: Free-form reason for ``"skipped"`` / ``"cas_exhausted"``.
    """

    status: PersistStatus
    old_commit: str | None = None
    new_commit: str | None = None
    conflict_files: list[str] = field(default_factory=list)
    reason: str | None = None


@contextlib.contextmanager
def _flock(path: Path) -> Iterator[None]:
    """Acquire an exclusive advisory file lock at *path*.

    Uses POSIX ``fcntl.flock`` (blocking). The lock is best-effort on
    filesystems that don't support flock (e.g. some NFS mounts); for the
    single-user local-dev / desktop scenario this is fine.

    The lock file is created (touched) if it does not already exist and is
    NOT removed on release -- removing it would race with concurrent lockers
    that opened it before we removed it. Leaving the file in place is
    harmless: it's a single empty file inside ``.git``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")  # noqa: SIM115 — context manager exits on yield
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


async def _run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr) as strings."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, stdout_b.decode().strip(), stderr_b.decode().strip()


async def _rev_parse(workspace: str, ref: str) -> str | None:
    """Return the commit hash for *ref* in *workspace*, or None if it does not exist."""
    rc, stdout, _ = await _run_git(["rev-parse", "--verify", ref], cwd=workspace)
    if rc != 0 or not stdout:
        return None
    return stdout


async def _branch_exists(workspace: str, branch: str) -> bool:
    """Return True iff a local branch named *branch* exists in *workspace*."""
    rc, _, _ = await _run_git(["rev-parse", "--verify", f"refs/heads/{branch}"], cwd=workspace)
    return rc == 0


async def capture_source_branch(workspace: str) -> str | None:
    """Return the current branch name of *workspace*, or None.

    Uses ``git symbolic-ref --short HEAD``. Returns None when:
      - the workspace is not a git repository,
      - HEAD is detached (no symbolic ref),
      - the git command fails for any other reason.
    """
    rc, stdout, _ = await _run_git(["symbolic-ref", "--short", "HEAD"], cwd=workspace)
    if rc != 0:
        return None
    return stdout or None


async def _conflict_files(worktree_path: str) -> list[str]:
    """Return the list of files with unresolved merge conflicts."""
    rc, stdout, _ = await _run_git(["diff", "--name-only", "--diff-filter=U"], cwd=worktree_path)
    if rc != 0 or not stdout:
        return []
    return [line for line in stdout.split("\n") if line]


async def merge_to_source_branch_via_detached_worktree(
    original_workspace: str,
    source_branch: str,
    exit_branch: str,
    max_cas_retries: int = 3,
    *,
    pre_cas_hook: PreCasHook | None = None,
) -> PersistResult:
    """Merge ``exit_branch`` into ``source_branch`` via a detached temp worktree.

    The merge runs in a freshly-created detached worktree, NOT in the user's
    main checkout. The source branch ref is advanced atomically via
    ``git update-ref refs/heads/<source-branch> <new> <expected-old>`` so
    concurrent writers cannot lose work. CAS failures retry up to
    ``max_cas_retries`` with a fresh temp worktree.

    A per-workspace file lock at ``<workspace>/.git/flowstate-persist.lock``
    serializes calls within the same workspace.

    Args:
        original_workspace: Absolute path to the original (non-worktree)
            git repository whose branch should be advanced.
        source_branch: Branch name to merge into (e.g. ``"main"``).
        exit_branch: Branch name to merge from (the exit task's worktree
            branch). Must exist in the repository.
        max_cas_retries: Maximum number of CAS attempts before giving up.
        pre_cas_hook: Test-only async hook called immediately before each
            ``git update-ref`` attempt. Production callers pass ``None``.
            Receives the zero-based attempt number.

    Returns:
        A PersistResult describing the outcome. Never raises for normal
        failure modes (conflicts, missing branches, CAS exhaustion).
    """
    lock_path = Path(original_workspace) / ".git" / "flowstate-persist.lock"

    # Verify the exit branch exists before taking the lock -- a missing exit
    # branch is a benign skip, not an error.
    if not await _branch_exists(original_workspace, exit_branch):
        return PersistResult(status="skipped", reason="exit_branch_missing")

    # The flock is blocking, so run it in a thread to avoid stalling the event
    # loop while we wait on another concurrent persist.
    return await asyncio.to_thread(
        _run_persist_blocking,
        original_workspace,
        source_branch,
        exit_branch,
        max_cas_retries,
        lock_path,
        pre_cas_hook,
    )


def _run_persist_blocking(
    original_workspace: str,
    source_branch: str,
    exit_branch: str,
    max_cas_retries: int,
    lock_path: Path,
    pre_cas_hook: PreCasHook | None,
) -> PersistResult:
    """Worker that holds the flock and drives the async merge via a loop.

    Runs in an ``asyncio.to_thread`` worker so the blocking ``flock`` does
    not stall the executor's event loop.
    """
    with _flock(lock_path):
        return asyncio.run(
            _persist_under_lock(
                original_workspace=original_workspace,
                source_branch=source_branch,
                exit_branch=exit_branch,
                max_cas_retries=max_cas_retries,
                pre_cas_hook=pre_cas_hook,
            )
        )


async def _persist_under_lock(
    original_workspace: str,
    source_branch: str,
    exit_branch: str,
    max_cas_retries: int,
    pre_cas_hook: PreCasHook | None,
) -> PersistResult:
    """Implement the CAS-retry merge loop. Lock is held by the caller."""
    last_old_commit: str | None = None
    for attempt in range(max_cas_retries):
        old_commit = await _rev_parse(original_workspace, f"refs/heads/{source_branch}")
        if old_commit is None:
            return PersistResult(status="skipped", reason="source_branch_missing")
        last_old_commit = old_commit

        temp_dir = tempfile.mkdtemp(
            prefix=f"flowstate-persist-{exit_branch[:8].replace('/', '_')}-"
        )
        try:
            rc, _stdout, stderr = await _run_git(
                ["worktree", "add", "--detach", temp_dir, old_commit],
                cwd=original_workspace,
            )
            if rc != 0:
                return PersistResult(
                    status="skipped",
                    old_commit=old_commit,
                    reason=f"failed to create temp worktree: {stderr}",
                )

            # Merge exit_branch into the detached HEAD.
            rc, _stdout, stderr = await _run_git(
                [
                    "merge",
                    "--no-ff",
                    "-m",
                    f"flowstate: persist {exit_branch}",
                    exit_branch,
                ],
                cwd=temp_dir,
            )
            if rc != 0:
                conflict_files = await _conflict_files(temp_dir)
                if conflict_files:
                    await _run_git(["merge", "--abort"], cwd=temp_dir)
                    return PersistResult(
                        status="conflict",
                        old_commit=old_commit,
                        conflict_files=conflict_files,
                    )
                # Non-conflict merge failure (e.g. exit_branch unreachable).
                return PersistResult(
                    status="skipped",
                    old_commit=old_commit,
                    reason=f"merge failed: {stderr}",
                )

            new_commit = await _rev_parse(temp_dir, "HEAD")
            if new_commit is None:
                return PersistResult(
                    status="skipped",
                    old_commit=old_commit,
                    reason="merge produced no HEAD",
                )

            # Deterministic injection seam for CAS-retry testing.
            if pre_cas_hook is not None:
                await pre_cas_hook(attempt)

            # Atomic CAS: refs/heads/<source-branch> : old_commit -> new_commit.
            rc, _stdout, stderr = await _run_git(
                [
                    "update-ref",
                    f"refs/heads/{source_branch}",
                    new_commit,
                    old_commit,
                ],
                cwd=original_workspace,
            )
            if rc == 0:
                return PersistResult(
                    status="advanced",
                    old_commit=old_commit,
                    new_commit=new_commit,
                )

            logger.info(
                "CAS failed for source branch %s on attempt %d/%d: %s",
                source_branch,
                attempt + 1,
                max_cas_retries,
                stderr,
            )
        finally:
            # Always remove the temp worktree before retrying or returning.
            await _run_git(
                ["worktree", "remove", "--force", temp_dir],
                cwd=original_workspace,
            )
            shutil.rmtree(temp_dir, ignore_errors=True)

    return PersistResult(
        status="cas_exhausted",
        old_commit=last_old_commit,
        reason=f"source branch moved repeatedly after {max_cas_retries} attempts",
    )
