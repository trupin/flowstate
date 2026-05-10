# Evaluation: STATE-013

**Date**: 2026-05-10
**Sprint**: sprint-037 (Phase 37c — Persist exit worktree to source branch)
**Verdict**: PASS

## Scope

STATE-013 adds a nullable `source_branch TEXT` column to `flow_runs`, with
`set_source_branch` / `get_source_branch` repository methods and a
`FlowRunRow.source_branch: str | None` field. No user-visible surface; the
column is groundwork for ENGINE-088 (not yet implemented). Behavioral
verification surface available now:

1. Server starts cleanly with the new schema on a fresh DB
2. Server starts cleanly against a pre-existing legacy DB (additive migration)
3. Repository round-trips set/get/clear/overwrite for the new column

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | Six-section log filled in (no placeholders) |
| Commands are specific and concrete | PASS | Names exact test functions (`test_flow_runs_has_source_branch_column`, etc.) and exact pytest output line counts |
| Real E2E (no mocks/TestClient) | PARTIAL | All evidence is pytest-level; no curl-against-running-server or sqlite3 inspection of the live `~/.flowstate/flowstate.db`. I performed the real E2E myself (see below) and it passes — the gap is in the agent's log, not the implementation. Acceptable for this issue because the new column has no user-visible surface yet, but flagged for the record. |
| Scenarios cover acceptance criteria | PASS | Every acceptance criterion has a named test, plus extras (overwrite, slashes-and-dots, unknown-id, independence) |
| Server restarted after changes | PASS | I verified this myself — see below |
| Reproduction logged before fix (bugs) | N/A | Feature, not bug |

The agent's log is heavy on pytest evidence and light on E2E-against-real-server.
For a foundation-only DB change with no user-visible API surface, that's defensible.
I closed the gap with my own real-server verification below.

## Independent Verification (Evaluator)

I exercised the migration against the **real** live DB and a fresh DB, not
through pytest. Procedure and results:

### 1. Pre-migration snapshot of live DB

```
$ sqlite3 ~/.flowstate/flowstate.db "PRAGMA user_version; PRAGMA table_info(flow_runs);"
1
0|id|TEXT|0||1
1|flow_definition_id|TEXT|1||0
...
14|task_id|TEXT|0||0
```
- 15 columns, no `source_branch`
- `user_version=1`
- Row count: 83 (cancelled=59, completed=24)

### 2. Real server restart applies migration

Killed the running server (PID 93682) and started a fresh `uv run flowstate
server` with the STATE-013 working tree. Server came up cleanly:

```
Starting Flowstate server on 127.0.0.1:9090
INFO:     Started server process [95026]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9090
INFO:     127.0.0.1:60477 - "GET /api/flows HTTP/1.1" 200 OK
```

### 3. Post-migration live DB state

```
$ sqlite3 ~/.flowstate/flowstate.db "PRAGMA user_version; PRAGMA table_info(flow_runs);"
2
...
15|source_branch|TEXT|0||0   <-- new column, TEXT, NOT NULL=0 (nullable), no default

$ sqlite3 ~/.flowstate/flowstate.db "SELECT COUNT(*) FROM flow_runs;"
83  <-- unchanged from pre-migration

$ sqlite3 ~/.flowstate/flowstate.db "SELECT status, COUNT(*) FROM flow_runs GROUP BY status;"
cancelled|59
completed|24   <-- distribution identical to pre-migration

$ sqlite3 ~/.flowstate/flowstate.db "SELECT COUNT(*) FROM flow_runs WHERE source_branch IS NULL;"
83   <-- all pre-existing rows have NULL

$ sqlite3 ~/.flowstate/flowstate.db "PRAGMA integrity_check; PRAGMA foreign_key_check;"
ok   <-- DB is consistent, no orphaned FKs
```

Migration is genuinely additive: zero data loss across 83 real production rows.

### 4. Idempotency

Restarted the server a second time. `user_version` stayed at `2`; `source_branch`
column count via `PRAGMA table_info(flow_runs) | grep -c source_branch` = `1`
(not 2 — column not added twice); row count still 83; server started cleanly.

### 5. Fresh-DB path

Built a brand-new DB in `$TMPDIR` via `FlowstateDB(db_path)` and a separate
in-memory DB. Both land at `user_version=2` with 16 columns including
`source_branch TEXT` — fresh and migration paths converge to the same schema.

### 6. Migration 1 regression check

The agent's log claims they rewrote migration 1's `INSERT ... SELECT *` to
enumerate columns explicitly, because the new column in `schema.sql` would
otherwise cause `table flow_runs_new has 15 columns but 16 values were
supplied`. I exercised this path by constructing `FlowstateDB(':memory:')`
(which walks both migration 1 and 2 from `user_version=0`). It succeeds and
arrives at the expected 16-column schema. Fix verified.

### 7. Round-trip via repository methods

Built a fresh DB, inserted a flow_definitions parent and a flow_runs row,
then exercised every advertised method:

```
default get_source_branch: None              <-- TEST-37c.5 default NULL
set/get main OK                               <-- TEST-37c.5 round-trip
FlowRunRow.source_branch='main'               <-- model reflects value
overwrite OK                                  <-- second set replaces
clear-with-None OK                            <-- TEST-37c.5 part 2: set(None) clears
unusual-chars round-trip OK (direct SQL confirms)  <-- 'feature/STATE-013.persist'
get(unknown) -> None OK                       <-- unknown id returns None
set(unknown) silent no-op OK                  <-- no exception on missing id
independence OK                               <-- per-run isolation
```

All behavior matches the issue spec verbatim.

### 8. Test-suite confirmation

```
$ uv run pytest tests/state/ -q
229 passed in 0.47s

$ uv run pytest tests/state/ -q -k "source_branch or user_version_at_least_two or flow_run_row_source_branch_field"
13 passed, 216 deselected
```

Matches the agent's claim.

## Acceptance Criteria Results

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | Schema migration adds `source_branch TEXT` column to `flow_runs` (nullable, no default) | PASS | `PRAGMA table_info` shows col 15 `source_branch TEXT`, `NOT NULL=0`, default empty |
| 2 | `FlowRunRow` includes `source_branch: str | None = None` | PASS | `db.get_flow_run(run_id).source_branch` returns `None` by default, `'main'` after set |
| 3 | `set_source_branch(flow_run_id, branch)` method | PASS | Round-trips set/get/clear/overwrite |
| 4 | `get_source_branch(flow_run_id) -> str \| None` method | PASS | Returns string, None on clear, None on unknown id |
| 5 | Existing rows: column NULL on pre-existing flow_runs | PASS | 83/83 pre-existing rows are NULL post-migration |
| 6 | All existing repository tests pass | PASS | 229 state tests pass, same count as agent claimed |
| 7 | New unit tests cover set+get round-trip and NULL handling | PASS | 13 new tests including round-trip, clear-with-None, unusual chars, unknown id, independence |

## Sprint Contract Test Results (Phase 37c, state-layer subset)

| Test | Criterion | Result |
|------|-----------|--------|
| TEST-37c.3 | Schema has `source_branch TEXT` nullable, no default | PASS — verified via `PRAGMA table_info(flow_runs)` on live and fresh DBs |
| TEST-37c.4 | Migration additive on pre-existing DB; existing row preserved with `source_branch IS NULL` | PASS — 83 real pre-existing rows migrated; all 83 NULL, no rows lost or rewritten |
| TEST-37c.5 | `set_source_branch` / `get_source_branch` round-trip; `set(None)` clears | PASS — verified directly via Python REPL against fresh DB |

The other 37c tests (TEST-37c.1, .2, .6 through .16) are out of scope for
STATE-013 — they belong to DSL-017 and ENGINE-088.

## Failures

None.

## Summary

7 of 7 acceptance criteria pass. The migration is genuinely additive on a real
83-row production DB (no data loss, all old rows NULL), idempotent across
restarts, and the repository methods round-trip correctly including edge cases
(NULL clear, unusual characters, unknown IDs, multi-run independence). The
agent's collateral fix to migration 1 (column enumeration instead of
`SELECT *`) is also verified to work, preventing a regression on fresh DBs.

The agent's E2E Verification Log is pytest-heavy with no curl/sqlite3 evidence
against the real running server, but for a foundation-only schema change with
no user-visible API surface this is a minor stylistic gap rather than a
disqualifying one. I closed the gap with my own real-server verification.

Verdict: **PASS**.
