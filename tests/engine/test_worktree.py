import subprocess
from pathlib import Path

import pytest

from flowstate.engine.worktree import (
    WorktreeError,
    WorktreeInfo,
    cleanup_worktree,
    create_worktree,
    init_git_repo,
    is_existing_worktree,
    is_git_repo,
    map_cwd_to_worktree,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


# --- is_git_repo ---


class TestIsGitRepo:
    def test_git_repo(self, git_repo: Path) -> None:
        assert is_git_repo(str(git_repo)) is True

    def test_not_git_repo(self, tmp_path: Path) -> None:
        assert is_git_repo(str(tmp_path)) is False

    def test_nonexistent_path(self) -> None:
        assert is_git_repo("/nonexistent/path") is False


# --- init_git_repo ---


class TestInitGitRepo:
    @pytest.mark.asyncio
    async def test_creates_valid_git_repo(self, tmp_path: Path) -> None:
        """init_git_repo should create a .git dir and an initial commit."""
        target = tmp_path / "workspace"
        target.mkdir()
        result = await init_git_repo(str(target))
        assert result is True
        assert is_git_repo(str(target))

    @pytest.mark.asyncio
    async def test_has_initial_commit(self, tmp_path: Path) -> None:
        """The repo should have exactly one commit after init."""
        target = tmp_path / "workspace"
        target.mkdir()
        await init_git_repo(str(target))
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=target,
            capture_output=True,
            text=True,
        )
        assert log.returncode == 0
        lines = log.stdout.strip().splitlines()
        assert len(lines) == 1
        assert "flowstate: init workspace" in lines[0]

    @pytest.mark.asyncio
    async def test_idempotent_on_existing_repo(self, git_repo: Path) -> None:
        """Calling init_git_repo on an existing repo should succeed (git init is idempotent)."""
        result = await init_git_repo(str(git_repo))
        # git init on existing repo is fine, commit may add a second commit
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_git_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return False if git binary is not available."""
        import asyncio

        target = tmp_path / "workspace"
        target.mkdir()

        original_exec = asyncio.create_subprocess_exec

        async def fake_exec(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        result = await init_git_repo(str(target))
        assert result is False
        monkeypatch.setattr(asyncio, "create_subprocess_exec", original_exec)

    @pytest.mark.asyncio
    async def test_worktree_works_after_init(self, tmp_path: Path) -> None:
        """After init_git_repo, worktree creation should succeed."""
        target = tmp_path / "workspace"
        target.mkdir()
        await init_git_repo(str(target))
        info = await create_worktree(str(target), "test-run-id-123")
        assert Path(info.worktree_path).exists()
        assert info.branch_name.startswith("flowstate/")
        await cleanup_worktree(info)


# --- is_existing_worktree ---


class TestIsExistingWorktree:
    def test_main_repo_is_not_worktree(self, git_repo: Path) -> None:
        assert is_existing_worktree(str(git_repo)) is False

    def test_worktree_detected(self, git_repo: Path) -> None:
        wt_path = git_repo.parent / "worktree"
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", "test-branch"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
        assert is_existing_worktree(str(wt_path)) is True
        # Cleanup
        subprocess.run(
            ["git", "worktree", "remove", str(wt_path)],
            cwd=git_repo,
            capture_output=True,
        )


# --- create_worktree ---


class TestCreateWorktree:
    @pytest.mark.asyncio
    async def test_create_success(self, git_repo: Path) -> None:
        info = await create_worktree(str(git_repo), "abc12345-def6-7890")
        assert Path(info.worktree_path).exists()
        assert info.branch_name == "flowstate/abc12345"
        assert info.original_workspace == str(git_repo.resolve())
        # Verify it's a real worktree
        assert (Path(info.worktree_path) / ".git").is_file()
        # Cleanup
        await cleanup_worktree(info)

    @pytest.mark.asyncio
    async def test_create_non_git_fails(self, tmp_path: Path) -> None:
        with pytest.raises(WorktreeError):
            await create_worktree(str(tmp_path), "test-run-id")

    @pytest.mark.asyncio
    async def test_branch_collision_uses_longer_id(self, git_repo: Path) -> None:
        # Create first worktree
        info1 = await create_worktree(str(git_repo), "abc12345-first")
        # Create second with same 8-char prefix
        info2 = await create_worktree(str(git_repo), "abc12345-second")
        assert info2.branch_name == "flowstate/abc12345-sec"
        # Cleanup
        await cleanup_worktree(info2)
        await cleanup_worktree(info1)


# --- cleanup_worktree ---


class TestCleanupWorktree:
    @pytest.mark.asyncio
    async def test_cleanup_removes_worktree(self, git_repo: Path) -> None:
        info = await create_worktree(str(git_repo), "cleanup-test1")
        wt_path = info.worktree_path
        assert Path(wt_path).exists()
        await cleanup_worktree(info)
        assert not Path(wt_path).exists()

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_does_not_raise(self, git_repo: Path) -> None:
        info = WorktreeInfo(
            original_workspace=str(git_repo),
            worktree_path="/nonexistent/worktree",
            branch_name="flowstate/nonexistent",
        )
        await cleanup_worktree(info)  # Should not raise


# --- map_cwd_to_worktree ---


class TestMapCwdToWorktree:
    def test_exact_match(self, tmp_path: Path) -> None:
        workspace = str(tmp_path / "repo")
        worktree = str(tmp_path / "wt")
        result = map_cwd_to_worktree(workspace, workspace, worktree)
        assert result == str(Path(worktree).resolve())

    def test_subdir_match(self, tmp_path: Path) -> None:
        workspace = str(tmp_path / "repo")
        worktree = str(tmp_path / "wt")
        cwd = str(tmp_path / "repo" / "src" / "lib")
        result = map_cwd_to_worktree(cwd, workspace, worktree)
        assert result == str(Path(worktree).resolve() / "src" / "lib")

    def test_no_match(self, tmp_path: Path) -> None:
        workspace = str(tmp_path / "repo")
        worktree = str(tmp_path / "wt")
        cwd = str(tmp_path / "other" / "path")
        result = map_cwd_to_worktree(cwd, workspace, worktree)
        assert result == cwd

    def test_partial_name_no_match(self, tmp_path: Path) -> None:
        """'/repo-extra' should NOT match '/repo' prefix."""
        workspace = str(tmp_path / "repo")
        worktree = str(tmp_path / "wt")
        cwd = str(tmp_path / "repo-extra")
        result = map_cwd_to_worktree(cwd, workspace, worktree)
        assert result == cwd
