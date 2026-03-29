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
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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


async def setup_worktree_if_needed(
    workspace: str,
    run_id: str,
    enable_worktree: bool,
) -> WorktreeInfo | None:
    """Create a worktree if the workspace is a git repo and worktree mode is enabled.

    Returns WorktreeInfo if a worktree was created, None otherwise.
    Catches WorktreeError internally and returns None on failure.
    """
    if not enable_worktree or not is_git_repo(workspace) or is_existing_worktree(workspace):
        return None
    try:
        return await create_worktree(workspace, run_id)
    except WorktreeError:
        logger.warning("Failed to create worktree, using workspace directly", exc_info=True)
        return None


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
