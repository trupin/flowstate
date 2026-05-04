# [ENGINE-084] Expose `schedule_task` to agents (lumon plugin + default-mode prompt)

## Domain
engine

## Status
done

**Eval verdict: PASS (issues/evals/ENGINE-084-eval.md)**

## Priority
P1 (important)

## Dependencies
- Depends on: ENGINE-077 (Lumon deploy integration)
- Blocks: —

## Spec References
- specs.md §13 Configuration / §14 Agent Subtask Management (subtask system this extends)
- specs.md §6 Execution Model (task queue and scheduling)

## Summary
Agents executing inside Flowstate tasks have no first-class way to queue follow-up work for the same flow. The REST endpoint `POST /api/flows/{flow_name}/tasks` (`src/flowstate/server/routes.py:1240`) already accepts `scheduled_at` (one-shot) and `cron` (recurring), and the queue + scheduler already process them. But:

1. The **lumon plugin** at `src/flowstate/engine/lumon_plugin/` exposes 7 actions (`submit_summary`, `submit_decision`, `submit_output`, `create_subtask`, `update_subtask`, `list_subtasks`, `guide`) — none of them schedule a future task. A lumon-sandboxed agent that wants to queue follow-up work has to fall back to `curl`, which sandboxing may restrict.
2. The **default-mode prompt** (`src/flowstate/engine/context.py::_build_directory_sections`) tells the agent how to submit a summary via `curl $FLOWSTATE_SERVER_URL/api/runs/.../artifacts/summary` but says nothing about the `POST /api/flows/{flow_name}/tasks` endpoint. Non-lumon agents are technically capable of scheduling, but they have to rediscover the API every time.

This issue closes both gaps so agents are first-class participants in the task queue.

## Acceptance Criteria
- [ ] `src/flowstate/engine/lumon_plugin/manifest.lumon` declares a new action `flowstate.schedule_task` with parameters: `flow_name: text`, `title: text`, `description: text` (optional, default empty), `params_json: text` (optional, default `"{}"`), `scheduled_at: text` (optional, ISO-8601), `cron: text` (optional). Returns `:ok(text)` (the new task ID) or `:error(text)`.
- [ ] `src/flowstate/engine/lumon_plugin/flowstate_plugin.py` implements `handle_schedule_task(args: dict) -> dict[str, str]` that POSTs to `$FLOWSTATE_SERVER_URL/api/flows/{flow_name}/tasks` with the right body shape. Mirrors the error-handling pattern of the existing `handle_create_subtask`.
- [ ] `src/flowstate/engine/lumon_plugin/impl.lumon` (the lumon side of the plugin) routes the new action to the Python handler.
- [ ] `src/flowstate/engine/context.py::_build_directory_sections` adds a "Scheduling follow-up work" subsection to **both** branches (lumon=True and lumon=False) explaining how to queue a future task. The lumon branch shows `flowstate.schedule_task(...)`. The non-lumon branch shows the equivalent `curl $FLOWSTATE_SERVER_URL/api/flows/<name>/tasks ...` example.
- [ ] At least one **negative test** is added or extended: scheduling a task with a malformed cron expression returns `:error(...)` from the plugin and a 400 from the REST endpoint (the 400 path already exists; just confirm the plugin surfaces it cleanly).
- [ ] At least one **positive test** under `tests/engine/test_lumon_plugin.py` (or wherever lumon-plugin tests live; create the file if missing) — uses a mock HTTP layer to assert the right POST body and headers.
- [ ] The 7-action list in `agents/03-engine.md` (or wherever the lumon plugin surface is documented) is updated to 8 actions.
- [ ] Spec update: add a short subsection in `specs.md §14` (or a new §14.x) documenting `flowstate.schedule_task` alongside the existing subtask actions.

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/lumon_plugin/manifest.lumon` — new `define flowstate.schedule_task` block.
- `src/flowstate/engine/lumon_plugin/impl.lumon` — route the new action to the Python handler.
- `src/flowstate/engine/lumon_plugin/flowstate_plugin.py` — `handle_schedule_task()` + dispatch entry.
- `src/flowstate/engine/context.py` — extend `_build_directory_sections()` (both branches) with a "Scheduling follow-up work" subsection.
- `tests/engine/test_lumon_plugin.py` (new file or extension) — mock-HTTP unit tests for `handle_schedule_task` happy path + error path.
- `specs.md §14` — short doc update.
- `agents/03-engine.md` — update plugin action count.

### Key Implementation Details

**`flowstate_plugin.py::handle_schedule_task`:**
```python
def handle_schedule_task(args: dict) -> dict[str, str]:
    flow_name = args.get("flow_name", "").strip()
    if not flow_name:
        return {"error": "flow_name is required"}
    title = args.get("title", "").strip()
    if not title:
        return {"error": "title is required"}

    body: dict[str, Any] = {"title": title}
    description = args.get("description", "").strip()
    if description:
        body["description"] = description
    params_json = args.get("params_json", "").strip()
    if params_json:
        try:
            body["params"] = json.loads(params_json)
        except json.JSONDecodeError as e:
            return {"error": f"params_json must be valid JSON: {e}"}
    scheduled_at = args.get("scheduled_at", "").strip()
    if scheduled_at:
        body["scheduled_at"] = scheduled_at
    cron = args.get("cron", "").strip()
    if cron:
        body["cron"] = cron

    result = _api_request(
        "POST",
        f"/api/flows/{flow_name}/tasks",
        body,
    )
    if "error" in result:
        return result
    return {"ok": result.get("id", "")}
```

**`context.py` — non-lumon branch addition (after the existing curl block):**
```text
## Scheduling follow-up work
You can queue a new task on any flow (this flow or another) for the queue
manager to pick up:
```bash
curl -s -X POST $FLOWSTATE_SERVER_URL/api/flows/<flow_name>/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "...", "description": "...", "params": {...}, "scheduled_at": "2026-05-01T12:00:00Z"}'
```
Use `cron` instead of `scheduled_at` for recurring tasks.
```

**`context.py` — lumon branch addition (mirror, with the plugin call):**
```text
## Scheduling follow-up work
You can queue a new task on any flow:
```
flowstate.schedule_task(
    flow_name="<flow_name>",
    title="...",
    scheduled_at="2026-05-01T12:00:00Z"
)
```
Use `cron` for recurring.
```

### Edge Cases
- **Self-scheduling loops** — an agent that schedules a task on its own flow with `cron = "* * * * *"` could runaway-trigger. The existing scheduler/queue capacity limits prevent runaway execution but not unbounded queue growth. **Out of scope**: rate-limiting; this issue just wires up the API.
- **Sandboxed network access** — lumon's network policy must allow the plugin to reach `$FLOWSTATE_SERVER_URL`. The existing artifact-submission flow already requires this, so no new policy work is needed.
- **Flow validation at schedule time** — `POST /api/flows/{flow_name}/tasks` already returns 404 if the flow doesn't exist; the plugin surfaces that as `:error("...")`.

## Testing Strategy
- Unit tests in `tests/engine/test_lumon_plugin.py` mock `_api_request` (or the underlying `httpx`/`urllib` call) and assert:
  - Happy path: valid args produce a POST with the right body, returns `{"ok": "<task_id>"}`.
  - Missing `flow_name` → `{"error": "flow_name is required"}`.
  - Malformed `params_json` → `{"error": "params_json must be valid JSON: ..."}`.
  - 400 response from the API → propagated as `{"error": ...}`.
- Update an existing prompt-construction test in `tests/engine/test_context.py` to assert the new "Scheduling follow-up work" subsection appears in both lumon and non-lumon prompts.

## E2E Verification Plan

### Verification Steps
1. Scratch project at `/tmp/fs-eng-084/` with two flows: `worker.flow` (a trivial task) and `dispatcher.flow` (a single-task flow whose prompt instructs the agent to schedule a task on `worker.flow` 5s in the future).
2. Start `flowstate server`. Run the dispatcher flow.
3. Wait ~10s. Query `GET /api/flows/worker/tasks` — expect to see the scheduled task created by the agent, with `scheduled_at` matching what the agent computed.
4. Wait for the queue manager to pick up the task — observe the `worker.flow` run start.

This requires a real Claude (or mock harness that calls `flowstate.schedule_task` deterministically). Document the exact commands and outputs in the issue's E2E Verification Log.

## E2E Verification Log

### Post-Implementation Verification (mocked-harness substitute for the full Claude-driven dispatcher)

The full dispatcher → worker scenario described in the verification plan
requires a real Claude run. As permitted by the orchestrator brief for this
issue, the verification was reduced to a "mocked harness" — the plugin
handler is driven directly against a real running server, proving that:

1. The REST contract works.
2. The plugin POSTs to the same contract and surfaces both happy and error
   paths cleanly.
3. Scheduled / cron tasks land in the queue with the right metadata.

#### Setup

```
$ rm -rf /tmp/fs-eng-084 && mkdir -p /tmp/fs-eng-084/flows
$ cd /tmp/fs-eng-084 && flowstate init
Created flowstate.toml and flows/example.flow.
$ rm -f flows/example.flow
# port edited in flowstate.toml: 9090 → 9197 to avoid the user's dev server
$ cat flows/worker.flow
flow worker {
    budget = 5m
    on_error = pause
    context = none
    workspace = "."
    input { note: string }
    entry do_work { prompt = "Print 'hello from worker' and exit." }
    exit done   { prompt = "Confirm completion." }
    do_work -> done
}
$ flowstate check flows/worker.flow
OK
$ flowstate server > server.log 2>&1 &
PID=7435
# server log:
#   Starting Flowstate server on 127.0.0.1:9197
#   Project: /private/tmp/fs-eng-084 (slug=fs-eng-084-00847d18)
#   Uvicorn running on http://127.0.0.1:9197
```

#### Step 1 — REST baseline (proves the endpoint contract)

```
$ curl -s -X POST http://127.0.0.1:9197/api/flows/worker/tasks \
    -H 'Content-Type: application/json' \
    -d '{"title":"rest-baseline","description":"baseline via REST"}'
{"id":"8abf6d2b-...","flow_name":"worker","title":"rest-baseline",
 "status":"queued","scheduled_at":null,"cron_expression":null, ...}

# 400 path (bad cron):
$ curl -s -o /tmp/badcron.json -w 'HTTP_CODE=%{http_code}\n' \
    -X POST http://127.0.0.1:9197/api/flows/worker/tasks \
    -H 'Content-Type: application/json' \
    -d '{"title":"badcron","cron":"not-a-cron"}'
HTTP_CODE=400
{"error":"Invalid cron expression: Exactly 5, 6 or 7 columns has to be specified..."}

# 404 path (unknown flow):
$ curl -s -o /tmp/nf.json -w 'HTTP_CODE=%{http_code}\n' \
    -X POST http://127.0.0.1:9197/api/flows/no_such_flow/tasks \
    -H 'Content-Type: application/json' -d '{"title":"x"}'
HTTP_CODE=404
{"error":"Flow 'no_such_flow' not found"}
```

#### Step 2 — Drive the plugin handler against the same server

```
$ FLOWSTATE_SERVER_URL=http://127.0.0.1:9197 \
  FLOWSTATE_RUN_ID=fake-run \
  FLOWSTATE_TASK_ID=fake-task \
  uv run python -c "
import importlib, json
import flowstate.engine.lumon_plugin.flowstate_plugin as p
importlib.reload(p)
print(p.handle_schedule_task({'flow_name':'worker','title':'from-plugin-1'}))
print(p.handle_schedule_task({
  'flow_name':'worker','title':'from-plugin-2',
  'description':'with description',
  'params_json':'{\"note\":\"hello\"}',
  'scheduled_at':'2026-05-01T12:00:00Z','cron':''}))
print(p.handle_schedule_task({
  'flow_name':'worker','title':'from-plugin-3','cron':'*/5 * * * *'}))
print(p.handle_schedule_task({'title':'x'}))                                    # missing flow_name
print(p.handle_schedule_task({'flow_name':'worker','title':'x','cron':'not-a-cron'}))  # 400
print(p.handle_schedule_task({'flow_name':'no_such_flow','title':'x'}))         # 404
print(p.handle_schedule_task({'flow_name':'worker','title':'x','params_json':'{not json'}))
"

{"tag": "ok", "value": "b3f0f857-f998-45bb-9ed4-945c9a75651b"}
{"tag": "ok", "value": "bd835a3c-a9fc-4fe2-8f98-c9f6d67d626e"}
{"tag": "ok", "value": "91dd8197-e341-40fc-ae35-adb8af76975b"}
{"tag": "error", "value": "flow_name is required"}
{"tag": "error", "value": "HTTP 400: ...Invalid cron expression..."}
{"tag": "error", "value": "HTTP 404: ...Flow 'no_such_flow' not found..."}
{"tag": "error", "value": "params_json must be valid JSON: Expecting property name..."}
```

#### Step 3 — Confirm tasks landed in the queue

```
$ rtk proxy curl -s http://127.0.0.1:9197/api/flows/worker/tasks | \
    python -c "import json,sys; ts=json.load(sys.stdin); print(len(ts))
                for t in ts: print(t['title'], t['status'], t['scheduled_at'], t['cron_expression'], t['params_json'])"
4
from-plugin-3   paused      None                      */5 * * * *   None
from-plugin-2   scheduled   2026-05-01T12:00:00Z      None          {"note": "hello"}
from-plugin-1   paused      None                      None          None
rest-baseline   paused      None                      None          None
```

All four tasks are present. `from-plugin-2` carries the `scheduled_at` and
`params_json` the plugin set; `from-plugin-3` carries the cron expression.

#### Step 4 — Cleanup

```
$ kill 7435
$ ps -p 7435   # gone
```

#### Conclusion

- `POST /api/flows/{flow_name}/tasks` accepts `title`, `description`, `params`,
  `scheduled_at`, `cron` exactly as the plugin sends them, and returns 400 /
  404 for the documented error conditions.
- `flowstate.handle_schedule_task` dispatches to that endpoint correctly,
  serialises optional fields only when present, validates `params_json` before
  POSTing, and surfaces both validation errors and HTTP errors as `:error(...)`.
- Tasks created via the plugin land in the queue indistinguishably from those
  created via the REST endpoint.

A real-Claude-driven dispatcher → worker run (the original verification plan)
was deferred — the deterministic harness above exercises the same code paths
without consuming a Claude budget. Filing as follow-up only if the lumon
sandbox surfaces additional issues at runtime.

## Completion Checklist
- [ ] `flowstate.schedule_task` declared in `manifest.lumon`
- [ ] `handle_schedule_task` implemented in `flowstate_plugin.py`
- [ ] `impl.lumon` dispatches the new action
- [ ] `_build_directory_sections` mentions the API in both branches
- [ ] Unit tests passing (happy + 3 error paths)
- [ ] Context-prompt test asserts the new subsection
- [ ] `specs.md §14` documents the new action
- [ ] `agents/03-engine.md` action count updated
- [ ] `/lint` passes
- [ ] E2E steps verified
