"""Unit tests for the lumon flowstate_plugin handlers.

We import the module fresh inside fixtures so we can patch its module-level
``SERVER_URL``/``RUN_ID``/``TASK_ID`` constants — they are read at import time
from environment variables.

The HTTP layer is mocked by patching ``urllib.request.urlopen`` inside the
plugin module, mirroring how the existing artifact-submission code talks to
the Flowstate API.
"""

from __future__ import annotations

import importlib
import json
from io import BytesIO
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


@pytest.fixture
def plugin_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """Re-import flowstate_plugin with env vars set so module-level constants pick them up."""
    monkeypatch.setenv("FLOWSTATE_SERVER_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("FLOWSTATE_RUN_ID", "run-123")
    monkeypatch.setenv("FLOWSTATE_TASK_ID", "task-456")
    import flowstate.engine.lumon_plugin.flowstate_plugin as mod

    importlib.reload(mod)
    try:
        yield mod
    finally:
        importlib.reload(mod)


def _http_response(status: int, body: str) -> MagicMock:
    """Build a mock urlopen context-manager that returns ``body`` with HTTP ``status``."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body.encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def _capture_post() -> tuple[list[dict[str, Any]], MagicMock]:
    """Build a side_effect that captures POST requests and returns 201 + a fixed task id."""
    captured: list[dict[str, Any]] = []

    def side_effect(req: Any, timeout: float = 30) -> MagicMock:
        del timeout  # parameter required by urlopen signature; unused here
        captured.append(
            {
                "url": req.full_url,
                "method": req.get_method(),
                "headers": dict(req.header_items()),
                "body": req.data.decode("utf-8") if req.data else None,
            }
        )
        return _http_response(201, json.dumps({"id": "task-new-789", "status": "queued"}))

    mock = MagicMock(side_effect=side_effect)
    return captured, mock


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestScheduleTaskHappyPath:
    def test_minimal_args_posts_correct_body(self, plugin_module: ModuleType) -> None:
        """A minimal valid call posts to /api/flows/<name>/tasks with title only."""
        captured, mock_urlopen = _capture_post()
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            result = plugin_module.handle_schedule_task(
                {"flow_name": "worker", "title": "Crawl docs"}
            )

        assert result == {"tag": "ok", "value": "task-new-789"}
        assert len(captured) == 1
        sent = captured[0]
        assert sent["url"] == "http://127.0.0.1:9999/api/flows/worker/tasks"
        assert sent["method"] == "POST"
        # urllib lowercases header names returned by header_items()
        assert any(
            k.lower() == "content-type" and v == "application/json"
            for k, v in sent["headers"].items()
        )
        body = json.loads(sent["body"])
        assert body == {"title": "Crawl docs"}

    def test_all_optional_fields_included(self, plugin_module: ModuleType) -> None:
        """description, params_json, scheduled_at, cron all flow into the POST body."""
        captured, mock_urlopen = _capture_post()
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            result = plugin_module.handle_schedule_task(
                {
                    "flow_name": "worker",
                    "title": "Crawl docs",
                    "description": "Pull README + spec",
                    "params_json": '{"repo": "my-app", "depth": 2}',
                    "scheduled_at": "2026-05-01T12:00:00Z",
                    "cron": "*/5 * * * *",
                }
            )

        assert result == {"tag": "ok", "value": "task-new-789"}
        body = json.loads(captured[0]["body"])
        assert body == {
            "title": "Crawl docs",
            "description": "Pull README + spec",
            "params": {"repo": "my-app", "depth": 2},
            "scheduled_at": "2026-05-01T12:00:00Z",
            "cron": "*/5 * * * *",
        }

    def test_blank_optional_fields_are_omitted(self, plugin_module: ModuleType) -> None:
        """Empty/whitespace optional fields are not sent in the body."""
        captured, mock_urlopen = _capture_post()
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            plugin_module.handle_schedule_task(
                {
                    "flow_name": "worker",
                    "title": "Crawl docs",
                    "description": "   ",
                    "params_json": "",
                    "scheduled_at": "  ",
                    "cron": "",
                }
            )
        body = json.loads(captured[0]["body"])
        assert body == {"title": "Crawl docs"}

    def test_strips_whitespace_from_required_fields(self, plugin_module: ModuleType) -> None:
        """flow_name and title are stripped before use."""
        captured, mock_urlopen = _capture_post()
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            plugin_module.handle_schedule_task({"flow_name": "  worker  ", "title": "  Crawl  "})
        assert captured[0]["url"] == "http://127.0.0.1:9999/api/flows/worker/tasks"
        assert json.loads(captured[0]["body"]) == {"title": "Crawl"}


# ---------------------------------------------------------------------------
# Validation errors (no HTTP call)
# ---------------------------------------------------------------------------


class TestScheduleTaskValidation:
    def test_missing_flow_name_returns_error(self, plugin_module: ModuleType) -> None:
        mock_urlopen = MagicMock()
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            result = plugin_module.handle_schedule_task({"title": "x"})
        assert result == {"tag": "error", "value": "flow_name is required"}
        mock_urlopen.assert_not_called()

    def test_blank_flow_name_returns_error(self, plugin_module: ModuleType) -> None:
        mock_urlopen = MagicMock()
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            result = plugin_module.handle_schedule_task({"flow_name": "   ", "title": "x"})
        assert result == {"tag": "error", "value": "flow_name is required"}
        mock_urlopen.assert_not_called()

    def test_missing_title_returns_error(self, plugin_module: ModuleType) -> None:
        mock_urlopen = MagicMock()
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            result = plugin_module.handle_schedule_task({"flow_name": "worker"})
        assert result == {"tag": "error", "value": "title is required"}
        mock_urlopen.assert_not_called()

    def test_malformed_params_json_returns_error(self, plugin_module: ModuleType) -> None:
        mock_urlopen = MagicMock()
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            result = plugin_module.handle_schedule_task(
                {"flow_name": "worker", "title": "x", "params_json": "{not json"}
            )
        assert result["tag"] == "error"
        assert "params_json must be valid JSON" in result["value"]
        mock_urlopen.assert_not_called()

    def test_params_json_must_be_object(self, plugin_module: ModuleType) -> None:
        """A JSON array (or other non-object) is rejected."""
        mock_urlopen = MagicMock()
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            result = plugin_module.handle_schedule_task(
                {"flow_name": "worker", "title": "x", "params_json": "[1, 2, 3]"}
            )
        assert result == {
            "tag": "error",
            "value": "params_json must decode to a JSON object",
        }
        mock_urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# API error propagation
# ---------------------------------------------------------------------------


class TestScheduleTaskApiErrors:
    def test_400_response_propagated_as_error(self, plugin_module: ModuleType) -> None:
        """A 400 from the REST endpoint (e.g. bad cron) surfaces as :error(...)."""
        err = HTTPError(
            url="http://127.0.0.1:9999/api/flows/worker/tasks",
            code=400,
            msg="Bad Request",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b'{"detail":"Invalid cron expression: bogus"}'),
        )
        with patch.object(plugin_module.urllib.request, "urlopen", side_effect=err):
            result = plugin_module.handle_schedule_task(
                {"flow_name": "worker", "title": "x", "cron": "not-a-cron"}
            )
        assert result["tag"] == "error"
        assert "HTTP 400" in result["value"]
        assert "Invalid cron expression" in result["value"]

    def test_404_when_flow_missing(self, plugin_module: ModuleType) -> None:
        """A 404 (flow not found) is surfaced verbatim."""
        err = HTTPError(
            url="http://127.0.0.1:9999/api/flows/missing/tasks",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b'{"detail":"Flow \'missing\' not found"}'),
        )
        with patch.object(plugin_module.urllib.request, "urlopen", side_effect=err):
            result = plugin_module.handle_schedule_task({"flow_name": "missing", "title": "x"})
        assert result["tag"] == "error"
        assert "HTTP 404" in result["value"]

    def test_response_without_id_returns_error(self, plugin_module: ModuleType) -> None:
        """If the API returns 200 but no id field, surface a clean error."""
        mock_urlopen = MagicMock(return_value=_http_response(201, json.dumps({})))
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            result = plugin_module.handle_schedule_task({"flow_name": "worker", "title": "x"})
        assert result == {
            "tag": "error",
            "value": "API response did not include a task id",
        }

    def test_response_with_invalid_json_returns_error(self, plugin_module: ModuleType) -> None:
        """If the body is not JSON, surface a clean error."""
        mock_urlopen = MagicMock(return_value=_http_response(201, "<<not json>>"))
        with patch.object(plugin_module.urllib.request, "urlopen", mock_urlopen):
            result = plugin_module.handle_schedule_task({"flow_name": "worker", "title": "x"})
        assert result["tag"] == "error"
        assert "unexpected response body" in result["value"]


# ---------------------------------------------------------------------------
# Environment validation
# ---------------------------------------------------------------------------


class TestEnvValidation:
    def test_missing_env_blocks_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If FLOWSTATE_SERVER_URL etc. are unset, the handler short-circuits."""
        monkeypatch.delenv("FLOWSTATE_SERVER_URL", raising=False)
        monkeypatch.delenv("FLOWSTATE_RUN_ID", raising=False)
        monkeypatch.delenv("FLOWSTATE_TASK_ID", raising=False)
        import flowstate.engine.lumon_plugin.flowstate_plugin as mod

        importlib.reload(mod)
        try:
            mock_urlopen = MagicMock()
            with patch.object(mod.urllib.request, "urlopen", mock_urlopen):
                result = mod.handle_schedule_task({"flow_name": "worker", "title": "x"})
            assert result["tag"] == "error"
            assert "FLOWSTATE_SERVER_URL" in result["value"]
            mock_urlopen.assert_not_called()
        finally:
            importlib.reload(mod)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class TestDispatcher:
    def test_schedule_task_registered_in_handlers(self, plugin_module: ModuleType) -> None:
        """The new action is wired into the HANDLERS dict so main() can dispatch it."""
        assert "schedule_task" in plugin_module.HANDLERS
        assert plugin_module.HANDLERS["schedule_task"] is plugin_module.handle_schedule_task

    def test_handlers_count_is_seven(self, plugin_module: ModuleType) -> None:
        """The dispatcher now exposes seven Python handlers (the eighth action,
        ``guide``, is implemented entirely in lumon and never reaches Python)."""
        assert len(plugin_module.HANDLERS) == 7
