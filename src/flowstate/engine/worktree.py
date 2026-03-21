"""Git worktree management for workspace isolation.

When a flow's workspace points to a git repository, each flow run gets its own
worktree so concurrent runs don't conflict. The worktree is created at run start
and cleaned up on completion/cancellation.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class WorktreeError(Exception):
    """Raised when worktree creation or cleanup fails."""


@dataclass
class WorktreeInfo:
    """Holds the worktree state for a flow run."""

    original_workspace: str  # Absolute path to the original git repo
    worktree_path: str  # Absolute path to the created worktree
    branch_name: str  # Branch name (flowstate/<run-id-prefix>)


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
