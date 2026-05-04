#!/usr/bin/env python3
"""Flowstate artifact submission backend for the Lumon plugin.

Called by impl.lumon via plugin.exec(). Reads arguments from stdin as JSON,
POSTs to the Flowstate API, and returns a JSON result to stdout.

Environment variables:
    FLOWSTATE_SERVER_URL: Base URL of the Flowstate server (e.g. http://127.0.0.1:8080)
    FLOWSTATE_RUN_ID: Current flow run ID
    FLOWSTATE_TASK_ID: Current task execution ID
"""

import contextlib
import json
import os
import sys
import urllib.error
import urllib.request

SERVER_URL = os.environ.get("FLOWSTATE_SERVER_URL", "")
RUN_ID = os.environ.get("FLOWSTATE_RUN_ID", "")
TASK_ID = os.environ.get("FLOWSTATE_TASK_ID", "")


def _check_env() -> str | None:
    """Return an error message if required env vars are missing, else None."""
    missing = []
    if not SERVER_URL:
        missing.append("FLOWSTATE_SERVER_URL")
    if not RUN_ID:
        missing.append("FLOWSTATE_RUN_ID")
    if not TASK_ID:
        missing.append("FLOWSTATE_TASK_ID")
    if missing:
        return f"Missing environment variables: {', '.join(missing)}"
    return None


def submit_artifact(name: str, content: str, content_type: str) -> dict[str, str]:
    """POST an artifact to the Flowstate API.

    Args:
        name: Artifact name (summary, decision, output).
        content: The artifact body.
        content_type: MIME type (text/markdown or application/json).

    Returns:
        A dict with "tag" and "value" keys for Lumon deserialization.
    """
    env_err = _check_env()
    if env_err:
        return {"tag": "error", "value": env_err}

    url = f"{SERVER_URL}/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/{name}"
    data = content.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"tag": "ok", "value": f"Submitted {name} (HTTP {resp.status})"}
    except urllib.error.HTTPError as e:
        body = ""
        with contextlib.suppress(Exception):
            body = e.read().decode("utf-8", errors="replace")[:500]
        return {"tag": "error", "value": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"tag": "error", "value": str(e)}


def handle_submit_summary(args: dict) -> dict[str, str]:
    """Submit a markdown summary artifact."""
    content = args.get("content", "")
    if not content:
        return {"tag": "error", "value": "content is required"}
    return submit_artifact("summary", content, "text/markdown")


def handle_submit_decision(args: dict) -> dict[str, str]:
    """Submit a JSON decision artifact."""
    target = args.get("target", "")
    reasoning = args.get("reasoning", "")
    confidence = args.get("confidence", 0.0)
    if not target:
        return {"tag": "error", "value": "target is required"}
    decision_json = json.dumps(
        {
            "decision": target,
            "reasoning": reasoning,
            "confidence": confidence,
        }
    )
    return submit_artifact("decision", decision_json, "application/json")


def handle_submit_output(args: dict) -> dict[str, str]:
    """Submit a JSON output artifact for cross-flow filing."""
    data = args.get("data", "")
    if not data:
        return {"tag": "error", "value": "data is required"}
    # Validate that data is valid JSON
    try:
        json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return {"tag": "error", "value": "data must be a valid JSON string"}
    return submit_artifact("output", data, "application/json")


def _api_request(
    method: str, path: str, body: str | None = None, content_type: str = "application/json"
) -> dict[str, str]:
    """Make an HTTP request to the Flowstate API."""
    env_err = _check_env()
    if env_err:
        return {"tag": "error", "value": env_err}

    url = f"{SERVER_URL}{path}"
    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    if body:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            return {"tag": "ok", "value": resp_body}
    except urllib.error.HTTPError as e:
        body_text = ""
        with contextlib.suppress(Exception):
            body_text = e.read().decode("utf-8", errors="replace")[:500]
        return {"tag": "error", "value": f"HTTP {e.code}: {body_text}"}
    except Exception as e:
        return {"tag": "error", "value": str(e)}


def handle_create_subtask(args: dict) -> dict[str, str]:
    """Create a new subtask."""
    title = args.get("title", "")
    if not title:
        return {"tag": "error", "value": "title is required"}
    path = f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/subtasks"
    result = _api_request("POST", path, json.dumps({"title": title}))
    if result["tag"] == "ok":
        try:
            data = json.loads(result["value"])
            return {"tag": "ok", "value": data.get("id", result["value"])}
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def handle_update_subtask(args: dict) -> dict[str, str]:
    """Update a subtask's status."""
    subtask_id = args.get("subtask_id", "")
    status = args.get("status", "")
    if not subtask_id:
        return {"tag": "error", "value": "subtask_id is required"}
    if status not in ("in_progress", "done"):
        return {"tag": "error", "value": "status must be 'in_progress' or 'done'"}
    path = f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/subtasks/{subtask_id}"
    return _api_request("PATCH", path, json.dumps({"status": status}))


def handle_list_subtasks(args: dict) -> dict[str, str]:
    """List all subtasks for the current task."""
    path = f"/api/runs/{RUN_ID}/tasks/{TASK_ID}/subtasks"
    return _api_request("GET", path)


def handle_schedule_task(args: dict) -> dict[str, str]:
    """Queue a follow-up task on a flow.

    Required: ``flow_name``, ``title``. Optional: ``description``, ``params_json``
    (a JSON-encoded object — invalid JSON yields an error), ``scheduled_at``
    (ISO-8601), ``cron``. Returns the new task's ID on success.
    """
    flow_name = str(args.get("flow_name", "")).strip()
    if not flow_name:
        return {"tag": "error", "value": "flow_name is required"}
    title = str(args.get("title", "")).strip()
    if not title:
        return {"tag": "error", "value": "title is required"}

    body: dict[str, object] = {"title": title}

    description = str(args.get("description", "")).strip()
    if description:
        body["description"] = description

    params_json = str(args.get("params_json", "")).strip()
    if params_json:
        try:
            parsed = json.loads(params_json)
        except (json.JSONDecodeError, TypeError) as e:
            return {"tag": "error", "value": f"params_json must be valid JSON: {e}"}
        if not isinstance(parsed, dict):
            return {"tag": "error", "value": "params_json must decode to a JSON object"}
        body["params"] = parsed

    scheduled_at = str(args.get("scheduled_at", "")).strip()
    if scheduled_at:
        body["scheduled_at"] = scheduled_at

    cron = str(args.get("cron", "")).strip()
    if cron:
        body["cron"] = cron

    path = f"/api/flows/{flow_name}/tasks"
    result = _api_request("POST", path, json.dumps(body))
    if result["tag"] != "ok":
        return result
    try:
        data = json.loads(result["value"])
    except (json.JSONDecodeError, TypeError):
        return {"tag": "error", "value": f"unexpected response body: {result['value'][:200]}"}
    task_id = data.get("id", "") if isinstance(data, dict) else ""
    if not task_id:
        return {"tag": "error", "value": "API response did not include a task id"}
    return {"tag": "ok", "value": task_id}


HANDLERS = {
    "submit_summary": handle_submit_summary,
    "submit_decision": handle_submit_decision,
    "submit_output": handle_submit_output,
    "create_subtask": handle_create_subtask,
    "update_subtask": handle_update_subtask,
    "list_subtasks": handle_list_subtasks,
    "schedule_task": handle_schedule_task,
}


def main() -> None:
    fn = sys.argv[1] if len(sys.argv) > 1 else ""
    args = json.load(sys.stdin)

    handler = HANDLERS.get(fn)
    result = handler(args) if handler else {"tag": "error", "value": f"Unknown function: {fn}"}
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
