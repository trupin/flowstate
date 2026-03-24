# Suite 13: Git Worktree Isolation (ENGINE-025)

**Timeout**: 10 minutes total
**Flow files needed**: `e2e_linear.flow` (with workspace overridden to git repo)
**Workspace**: `/tmp/flowstate-e2e-worktree` (a git-initialized directory)

## Purpose

Verify that when a flow's workspace is a git repository, the executor creates an isolated git worktree for the run. Each flow run should get its own branch and working directory so concurrent runs don't conflict.

This tests ENGINE-025 (workspace/data-dir separation + git worktree isolation).

## Procedure

### 1. Create a git workspace

Before starting the server, initialize a git repository as the workspace:

```bash
mkdir -p /tmp/flowstate-e2e-worktree
cd /tmp/flowstate-e2e-worktree
git init
git config user.email "test@test.com"
git config user.name "Test"
echo "initial" > README.md
git add .
git commit -m "init"
```

### 2. Create a custom flow file

Create a flow that uses the git workspace:

```bash
cat > flows/e2e_worktree.flow << 'EOF'
flow e2e_worktree {
    budget = 5m
    on_error = pause
    context = handoff
    workspace = "/tmp/flowstate-e2e-worktree"
    skip_permissions = true
    worktree = true

    entry start {
        prompt = """
        Create a file called worktree_test.txt with the text "Worktree isolation works".
        """
    }

    exit done {
        prompt = """
        Read worktree_test.txt and write SUMMARY.md confirming its contents.
        """
    }

    start -> done
}
EOF
```

Wait for the file watcher to discover the flow.

### 3. Launch Playwright and start the flow

Start the flow via UI as in other suites.

### 4. Monitor execution

Poll until completion or timeout.

### 5. Verify worktree creation

After the flow completes, check:

**5a. Worktree path in API response:**
```python
resp = httpx.get(f"http://127.0.0.1:9090/api/runs/{run_id}")
data = resp.json()
worktree_path = data.get("worktree_path")
print(f"Worktree path: {worktree_path}")
```

The `worktree_path` field should be non-null and point to a temporary directory (e.g., `/tmp/flowstate-abc123-...`).

**5b. Task cwd uses worktree path:**
```python
for task in data.get("tasks", []):
    print(f"  {task['node_name']}: cwd={task.get('cwd')}")
```

Each task's `cwd` should be the worktree path (not the original `/tmp/flowstate-e2e-worktree`).

**5c. Git branch created:**
```bash
cd /tmp/flowstate-e2e-worktree
git branch --list "flowstate/*"
```

A branch like `flowstate/<run-id-prefix>` should have been created (even if later cleaned up — check git reflog).

**5d. Output file in worktree (or merged back):**
```bash
# Check if the file was created (in worktree or main repo)
ls /tmp/flowstate-e2e-worktree/worktree_test.txt 2>/dev/null && echo "File in main repo"
# Check if worktree was cleaned up
git worktree list 2>/dev/null
```

**5e. Worktree cleanup:**
The worktree should be cleaned up after flow completion (if `worktree_cleanup = true`, which is the default). Check:
```bash
# Worktree directory should be removed
test -d "$worktree_path" && echo "Worktree still exists (cleanup failed)" || echo "Worktree cleaned up"
```

### 6. Verify no worktree for non-git workspace

As a control, verify that the regular `e2e_linear` flow (workspace `/tmp/flowstate-e2e-linear`, not a git repo) does NOT create a worktree:

```python
# Start another run with e2e_linear
# Check that worktree_path is null in the response
```

### 7. Verify in UI

Check the run detail page:
- Node details should show the worktree path as `cwd` (not the original workspace)
- The `worktree_path` field should be visible if the node detail panel exposes it

Take screenshots:
```python
page.screenshot(path="/tmp/flowstate-e2e-worktree-final.png", full_page=True)
```

### 8. Clean up

```python
context.close()
browser.close()
```

```bash
rm -rf /tmp/flowstate-e2e-worktree
```

## Success Criteria

- [ ] Flow starts and completes with git workspace
- [ ] `worktree_path` is set in the run detail API response
- [ ] Task cwds point to the worktree directory (not the original repo)
- [ ] Git branch `flowstate/<prefix>` was created
- [ ] Worktree is cleaned up after flow completion
- [ ] Non-git workspaces do NOT create worktrees (control check)
- [ ] No stale tasks detected
- [ ] Completed within 10-minute timeout
