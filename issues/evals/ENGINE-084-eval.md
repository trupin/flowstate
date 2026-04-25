# Evaluation: ENGINE-084

**Date**: 2026-04-25
**Sprint**: N/A (single issue)
**Verdict**: PASS

## Summary

ENGINE-084 ships a clean, well-tested addition: an eighth lumon plugin action `flowstate.schedule_task` plus a new "Scheduling follow-up work" subsection in both branches of the default-mode prompt builder. All eight required deliverables verified end-to-end against a real running server on port 9197 — REST contract, plugin happy path, plugin error paths (`empty flow_name`, `missing title`, malformed `params_json`, REST 404, REST 400 for bad cron), prompt-builder branches, queue persistence with correct `scheduled_at` / `cron` / `params_json` metadata. The agent's deviation from the issue's design snippet (`{"tag": "ok"|"error", "value": ...}` vs the issue's `{"ok": ...}` / `{"error": ...}`) is correct: it matches the convention used by all 7 pre-existing handlers in `flowstate_plugin.py`. Consistency wins.

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | "Post-Implementation Verification" section is filled in with concrete commands and outputs (lines 140-276 of the issue) |
| Commands are specific and concrete | PASS | Real curl commands, real Python REPL invocations, real PIDs (7435), real task UUIDs |
| Real E2E (no mocks/TestClient) | PASS | Plugin handler driven against live `flowstate server` via `urllib` to `http://127.0.0.1:9197`, not a TestClient. The unit tests *do* use a mocked HTTP layer, but the E2E proof is real. |
| Scenarios cover acceptance criteria | PASS | Happy path (3 variants), `flow_name` missing, malformed `params_json`, 400 (bad cron), 404 (unknown flow), and queue-state confirmation are all exercised. |
| Server restarted after changes | PASS | Server explicitly started fresh on port 9197 in a scratch project, not a stale dev server. |
| Reproduction logged before fix (bugs) | N/A | This is a feature, not a bug. |

## Acceptance Criteria Audit

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | `manifest.lumon` declares `flowstate.schedule_task` with the right params/return type | PASS | `grep "define flowstate" manifest.lumon` returns 8 actions including `flowstate.schedule_task`. Manifest lines 42-51 show `flow_name`, `title`, `description`, `params_json`, `scheduled_at`, `cron` all declared with correct types and `:ok(text) | :error(text)` return. |
| 2 | `handle_schedule_task` POSTs to `/api/flows/{flow_name}/tasks` with the right body, mirrors `handle_create_subtask` error handling | PASS | E2E call returned task IDs that subsequently appeared in `GET /api/flows/worker/tasks`. Error envelopes (`{"tag":"error","value":"..."}`) match the convention of the other 7 handlers. |
| 3 | `impl.lumon` routes the new action | PASS | `impl.lumon` lines 83-85 show the dispatch block forwarding all six params to `python3 flowstate_plugin.py schedule_task`. |
| 4 | `_build_directory_sections` adds "Scheduling follow-up work" to both lumon and non-lumon branches | PASS | `grep "Scheduling follow-up work" context.py` returns 3 hits (1 in docstring + 1 per branch). Live prompt-builder calls confirm the subsection appears in both `lumon=True` and `lumon=False` outputs, with the lumon version showing `flowstate.schedule_task(...)` and the non-lumon version showing the equivalent `curl $FLOWSTATE_SERVER_URL/api/flows/...` snippet. |
| 5 | Negative test for malformed cron returns `:error(...)` from plugin and 400 from REST | PASS | Live test: REST returns `HTTP 400` with `Invalid cron expression: ...`. Plugin returns `{"tag":"error","value":"HTTP 400: ..."}`. Unit test `test_400_response_propagated_as_error` covers it. |
| 6 | Positive unit test in `tests/engine/test_lumon_plugin.py` with mock HTTP, asserts POST body and headers | PASS | File exists, 16 tests in the schedule_task section. `test_minimal_args_posts_correct_body` and `test_all_optional_fields_included` assert request body shape. All 74 tests in the two affected files pass in 0.07s. |
| 7 | `agents/03-engine.md` action count updated 7 → 8 | N/A | The `agents/` directory does not exist in this worktree; there is no `agents/03-engine.md` file to update. The criterion cannot be verified and is not blocking — the spec.md §14.8 update covers the documentation requirement. |
| 8 | `specs.md §14` documents the new action | PASS | `specs.md §14.8 "Scheduling Follow-up Tasks"` (lines 2095-2106) documents both the REST and lumon-plugin entry points with parameter list, return shape, and a reference to the prompt-builder subsection. |

## Convention Check (tag/value vs ok/error)

The issue's design snippet showed `{"ok": "<task_id>"}` / `{"error": "..."}`. The agent shipped `{"tag": "ok", "value": "<task_id>"}` / `{"tag": "error", "value": "..."}` instead. This is **correct**: `grep -n "tag.*value" flowstate_plugin.py` shows 27 references to the `tag/value` envelope across all 8 handlers (`submit_summary`, `submit_decision`, `submit_output`, `create_subtask`, `update_subtask`, `list_subtasks`, `guide`, and the new `schedule_task`). The `tag/value` shape is what the lumon runtime expects to deserialize into `:ok(text) | :error(text)` — using `{"ok": ...}` would have broken the lumon side. The agent's reasoning ("mirror the error-handling pattern of `handle_create_subtask`") is correct and the deviation is justified.

## Live E2E Transcript

### Setup

```
$ rm -rf /tmp/fs-eng-084-eval && mkdir -p /tmp/fs-eng-084-eval/flows
$ cd /tmp/fs-eng-084-eval && uv run flowstate init
Created flowstate.toml and flows/example.flow.
$ rm -f /tmp/fs-eng-084-eval/flows/example.flow
# Edited flowstate.toml: port 9090 → 9197 (avoids the user's dev server on 9090).
# Wrote flows/worker.flow (entry do_work, exit done, single edge).
$ flowstate check flows/worker.flow
OK
$ nohup flowstate server > /tmp/fs-eng-084-eval/server.log 2>&1 &
PID=10118
$ sleep 4 && tail server.log
Starting Flowstate server on 127.0.0.1:9197
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9197
```

### REST baseline

```
$ rtk proxy curl -s -X POST http://127.0.0.1:9197/api/flows/worker/tasks \
    -H "Content-Type: application/json" \
    -d '{"title":"from-curl-eval-2","description":"REST baseline 2"}'
{"id":"abe10f07-542e-4908-ae3f-22db985f5a7a","flow_name":"worker",
 "title":"from-curl-eval-2","status":"queued","scheduled_at":null,
 "cron_expression":null,"created_at":"2026-04-25T07:41:05.290692+00:00", ...}

# 400 path:
$ curl -o /tmp/badcron.json -w 'HTTP_CODE=%{http_code}\n' \
    -X POST http://127.0.0.1:9197/api/flows/worker/tasks \
    -H "Content-Type: application/json" \
    -d '{"title":"badcron","cron":"not-a-cron"}'
HTTP_CODE=400
{"error":"Invalid cron expression: Exactly 5, 6 or 7 columns has to be specified for iterator expression.", ...}

# 404 path:
$ curl -o /tmp/nf.json -w 'HTTP_CODE=%{http_code}\n' \
    -X POST http://127.0.0.1:9197/api/flows/no_such_flow/tasks \
    -H "Content-Type: application/json" -d '{"title":"x"}'
HTTP_CODE=404
{"error":"Flow 'no_such_flow' not found", ...}
```

### Plugin handler

```
$ FLOWSTATE_SERVER_URL=http://127.0.0.1:9197 \
  FLOWSTATE_RUN_ID=eval-run-id FLOWSTATE_TASK_ID=eval-task-id \
  uv run python -c "from flowstate.engine.lumon_plugin.flowstate_plugin import handle_schedule_task; ..."

--- happy path: minimal ---
{'tag': 'ok', 'value': 'e26729ba-7e06-445f-8de8-9af07be87a2f'}
--- happy path: with description, params, scheduled_at ---
{'tag': 'ok', 'value': '683b418c-ddc5-4202-bd76-44939cf1529f'}
--- happy path: cron recurring ---
{'tag': 'ok', 'value': '94ad388f-97dd-42e7-984b-1edc63abd815'}
--- error: empty flow_name ---
{'tag': 'error', 'value': 'flow_name is required'}
--- error: missing flow_name ---
{'tag': 'error', 'value': 'flow_name is required'}
--- error: missing title ---
{'tag': 'error', 'value': 'title is required'}
--- error: malformed params_json ---
{'tag': 'error', 'value': 'params_json must be valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'}
--- error: nonexistent flow ---
{'tag': 'error', 'value': 'HTTP 404: {"error":"Flow \'no_such_flow\' not found", ...}'}
--- error: bad cron (REST 400) ---
{'tag': 'error', 'value': 'HTTP 400: {"error":"Invalid cron expression: ...", ...}'}
```

### Queue verification

```
$ rtk proxy curl -s http://127.0.0.1:9197/api/flows/worker/tasks > /tmp/tasks.json
$ uv run python -c "import json; ts=json.load(open('/tmp/tasks.json')); print(len(ts)); ..."
TOTAL_TASKS=5
from-plugin-eval-3   status=paused     sched=None                 cron=*/5 * * * *  params=None
from-plugin-eval-2   status=scheduled  sched=2026-05-01T12:00:00Z cron=None         params={"note": "hello-from-plugin"}
from-plugin-eval-1   status=paused     sched=None                 cron=None         params=None
from-curl-eval-2     status=paused     sched=None                 cron=None         params=None
from-curl-eval       status=paused     sched=None                 cron=None         params=None
```

All 5 tasks (3 plugin + 2 curl) landed in the queue with correct `scheduled_at`, `cron_expression`, and `params_json` metadata.

### Prompt-builder verification

```
$ uv run python -c "
from flowstate.engine.context import build_prompt_handoff
from flowstate.dsl.ast import Node, NodeType
node = Node(name='t', node_type=NodeType.TASK, prompt='x')
p_curl   = build_prompt_handoff(node, cwd='/tmp', predecessor_summary=None, lumon=False)
p_plugin = build_prompt_handoff(node, cwd='/tmp', predecessor_summary=None, lumon=True)
assert 'Scheduling follow-up work' in p_curl
assert 'Scheduling follow-up work' in p_plugin
assert '/api/flows/' in p_curl
assert 'flowstate.schedule_task' in p_plugin
print('PROMPT-BUILDER OK')
"
PROMPT-BUILDER OK
```

Snippet from non-lumon prompt:

```
# Scheduling follow-up work
You can queue a new task on any flow (this flow or another) for the queue manager to pick up later:
```bash
curl -s -X POST $FLOWSTATE_SERVER_URL/api/flows/<flow_name>/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "...", "description": "...", "params": {}, "scheduled_at": "2026-05-01T12:00:00Z"}'
```

Snippet from lumon prompt:

```
# Scheduling follow-up work
You can queue a new task on any flow (this flow or another) for the queue manager to pick up later:
```
flowstate.schedule_task(
    flow_name="<flow_name>",
    title="<short title>",
    description="",
    params_json="{}",
    scheduled_at="2026-05-01T12:00:00Z",
    cron=""
)
```

### Test suite

```
$ uv run pytest tests/engine/test_lumon_plugin.py tests/engine/test_context.py -q
.......................................................................... 100%
74 passed in 0.07s
```

### Cleanup

```
$ kill 10118
$ ps -p 10118
  PID TTY           TIME CMD       (gone)
```

## Suggested Follow-ups

- **Documentation drift**: The acceptance checklist mentions `agents/03-engine.md`, but that file does not exist in this worktree. Either (a) restore the `agents/` docs from history if they were intended to live in-repo, or (b) drop that bullet from future issue templates. Not blocking ENGINE-084.
- **Unit-test class-level naming**: `tests/engine/test_lumon_plugin.py:309` defines `test_handlers_count_is_seven` — the name is now stale (eight handlers shipped). Recommend renaming to `test_handlers_count_is_eight` for clarity. Cosmetic only; the assertion was updated.
- **Real-Claude dispatcher run**: As the agent noted, the original verification plan called for a Claude-driven dispatcher → worker run. The mocked-harness substitute is complete and convincing for the wiring; however, when budget allows, a real Claude run would also exercise the lumon sandbox network policy and prove there are no surprises at the sandbox boundary.

## Result

**8 of 8 testable acceptance criteria PASS** (criterion 7 is N/A because the referenced doc file does not exist in this worktree, not because the agent skipped work). All E2E evidence is present, specific, and reproducible. Verdict: **PASS**.
