---
description: Run real (unmocked) E2E tests of the Flowstate system using Playwright + real Claude Code subprocesses. Tests the full stack from UI to subprocess execution.
argument-hint: "[suite-name|all|list]"
user_invocable: true
---

Run real end-to-end tests of Flowstate using Playwright for UI interaction and real Claude Code subprocesses (no mocking).

## UI-Only Principle

**All actions in the app MUST go through the UI via Playwright.** Never use direct API calls (`curl`, `httpx`, `urllib`) to perform actions like starting runs, pausing, resuming, or cancelling. Instead, click buttons, fill forms, and interact with the UI exactly as a real user would.

- **Submitting a task**: Click flow in sidebar → click "Submit Task" → fill title/description in TaskModal → submit. The queue manager picks it up automatically (flow must be enabled).
- **Enabling/disabling a flow**: Click the enable/disable toggle in the flow detail header
- **Pausing a task**: Click the task in the queue → pause button on task detail
- **Cancelling a task**: Click the task → cancel button, or `POST /api/tasks/{id}/cancel`
- **Monitoring task status**: Poll `GET /api/tasks/{id}` for status and current_node changes

API calls are only permitted for:
- **Prerequisites check**: `GET /api/flows` to verify server health before launching Playwright
- **Staleness detection**: `GET /api/runs/{id}` and log endpoints during polling loops, since precise timestamp comparison is impractical via DOM alone
- **Process cleanup verification**: `pgrep` commands to check for orphan subprocesses (not an API call)

## 1. Parse arguments

Parse `$ARGUMENTS` to determine which suites to run:

- **No args or `all`**: Run all 13 suites sequentially
- **Suite name**: Run just that one suite. Valid names:
  - `smoke` → `suites/01-smoke.md`
  - `linear` → `suites/02-linear-flow.md`
  - `conditional` → `suites/03-conditional-flow.md`
  - `fork-join` → `suites/04-fork-join-flow.md`
  - `controls` → `suites/05-flow-controls.md`
  - `cancel` → `suites/06-cancel-cleanup.md`
  - `error` → `suites/07-error-handling.md`
  - `websocket` → `suites/08-websocket-events.md`
  - `cycle` → `suites/09-cycle-flow.md`
  - `activity` → `suites/10-activity-logs.md`
  - `full-stack` → `suites/11-ui-features.md`
  - `self-report` → `suites/12-self-report-routing.md`
  - `worktree` → `suites/13-worktree-isolation.md`
  - `task-lifecycle` → `suites/14-task-lifecycle.md`
  - `cross-flow` → `suites/15-cross-flow-filing.md`
  - `enable-disable` → `suites/16-enable-disable.md`
- **`list`**: Print the suite list with descriptions and stop:
  ```
  Available E2E test suites:
    smoke        Server + UI loads, flows discovered, navigation works
    linear       3-node linear flow completes end-to-end
    conditional  Judge-based conditional routing decision
    fork-join    Parallel fork-join execution
    controls     Pause and resume a running flow
    cancel       Cancel + verify subprocess cleanup
    error        on_error=pause with failing task
    websocket    Real-time log streaming in UI
    cycle        Cyclic flow with judge exit
    activity     Executor activity logs (ENGINE-024) in UI console
    full-stack   Full-stack feature verification: engine, server, UI
    self-report  Self-report routing without judge (ENGINE-023, DSL-007)
    worktree     Git worktree isolation for concurrent runs (ENGINE-025)
  ```

## 2. Prerequisites check

Before running any suite, verify:

1. **`claude` CLI is available**: `which claude` must succeed
2. **Playwright chromium is installed**: Run `uv run python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); p.chromium.launch(headless=True).close(); p.stop(); print('OK')"`. If it fails, run `uv run playwright install chromium`.
3. **UI dependencies installed**: `test -d ui/node_modules` or run `cd ui && npm install`
4. **E2E dependencies**: `uv sync --group e2e` to ensure playwright is available

If any prerequisite fails and cannot be auto-fixed, report the error and stop.

## 3. Suite execution loop

For each selected suite, execute these steps **sequentially**:

### 3a. Server restart

Follow `procedures/server-restart.md` completely. This:
- Kills existing server and orphan processes
- Deletes the database for a clean slate
- Rebuilds the UI
- Starts a fresh server on port 9090
- Copies the suite's required flow files to `./flows/`
- Creates the suite's workspace directory

Which flow files each suite needs:
- `smoke`, `linear`, `controls`, `cancel`, `websocket`, `activity`, `full-stack`: `e2e_linear.flow`
- `conditional`: `e2e_conditional.flow`
- `fork-join`: `e2e_fork_join.flow`
- `error`: `e2e_error.flow`
- `cycle`: `e2e_cycle.flow`
- `self-report`: `e2e_self_report.flow`
- `worktree`: creates its own flow file inline (uses a git-initialized workspace)

### 3b. Run the suite

Read the suite file from `suites/NN-name.md` and follow its procedure step by step.

**During execution, track:**
- Start time (for duration reporting)
- Pass/fail status
- Screenshots taken (paths)
- Bugs found (descriptions)
- Any stale tasks detected

### 3c. Staleness monitoring

For any suite that starts a flow run, apply `procedures/staleness-detection.md` during the polling loop. Check every 30 seconds.

### 3d. Cleanup on failure

If a suite fails or times out:
1. If a flow is still running, cancel it via the UI — click the "Cancel" button in the run detail controls
2. Run `procedures/process-cleanup.md` to verify no orphan processes
3. Take a screenshot of the current UI state
4. Record the failure details

### 3e. Issue filing

If a **bug** is found (not just a timeout or expected behavior):
1. Follow `procedures/issue-filing.md`
2. Create the issue file
3. Propose a fix plan
4. Record the issue ID for the summary

**What counts as a bug:**
- Orphan subprocess after cancel → engine bug
- UI not reflecting state change → ui bug
- API returning wrong status → server bug
- Flow stuck with no progress and no error → engine bug
- WebSocket events not arriving → server bug
- JavaScript console errors → ui bug

**What is NOT a bug:**
- Slow Claude responses (timeout but no staleness)
- Claude working around a designed failure (smart agent behavior)
- Judge making an unexpected but valid routing decision

## 4. Summary

After all suites complete, print a summary report:

```
═══════════════════════════════════════════════════
  FLOWSTATE E2E TEST RESULTS
═══════════════════════════════════════════════════

Suite Results:
  [{STATUS}] {suite_name}  ({duration}) {— failure reason if any}

Passed: N/M
Failed: N
Stale:  N

Issues Filed:
  {ISSUE-ID}: {Title}
  ...

Fix Plans Proposed:
  {ISSUE-ID}: {One-line summary of proposed fix}
  ...

Screenshots:
  {path}: {description}
  ...

Total wall time: {total_duration}
═══════════════════════════════════════════════════
```

Status values:
- `PASS` — all success criteria met
- `FAIL` — a success criterion was not met (bug found)
- `STALE` — a task was detected as stale and cancelled
- `TIMEOUT` — suite exceeded its timeout (not necessarily a bug)
- `SKIP` — suite was skipped (prerequisite failure)

## 5. Final cleanup

After the summary:
1. Stop the server: kill the PID from `/tmp/flowstate-backend.pid`
2. Kill any remaining orphan claude processes
3. Clean up workspace directories: `rm -rf /tmp/flowstate-e2e-*`

## Notes

- **Cost awareness**: Each suite spawns real Claude Code subprocesses that consume API credits. Short budgets (5-10m) and simple prompts minimize cost.
- **Non-determinism**: Real Claude responses vary between runs. Suites are designed to pass regardless of specific Claude output — they test infrastructure, not Claude behavior.
- **Wall time**: A full `all` run takes roughly 30-60 minutes depending on Claude response times.
- **Screenshots**: All screenshots are saved to `/tmp/flowstate-e2e-*` for review.
