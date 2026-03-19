"""Tests for the Flowstate CLI (typer commands)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from flowstate.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


# ---------------------------------------------------------------------------
# Valid .flow source used across multiple tests
# ---------------------------------------------------------------------------
VALID_FLOW_SOURCE = """\
flow setup_project {
    budget = 30m
    on_error = pause
    context = session
    workspace = "./project"

    entry scaffold {
        prompt = "Create project"
    }

    exit done {
        prompt = "Finalize"
    }

    scaffold -> done
}
"""

INVALID_FLOW_SOURCE = """\
this is not valid flow syntax at all!!!
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_flow(tmp_path: Path, content: str, name: str = "test.flow") -> Path:
    """Write a .flow file and return its path."""
    p = tmp_path / name
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# 1. Help
# ---------------------------------------------------------------------------


class TestHelp:
    def test_help_lists_all_commands(self) -> None:
        """flowstate --help prints help with all 7 commands listed."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ["check", "server", "run", "runs", "status", "schedules", "trigger"]:
            assert cmd in result.output


# ---------------------------------------------------------------------------
# 2-5. check command
# ---------------------------------------------------------------------------


class TestCheckValidFile:
    def test_check_valid_flow_file(self, tmp_path: Path) -> None:
        """flowstate check <valid.flow> outputs OK and exits 0."""
        flow_file = _write_flow(tmp_path, VALID_FLOW_SOURCE)
        result = runner.invoke(app, ["check", str(flow_file)])
        assert result.exit_code == 0
        assert "OK" in result.output


class TestCheckParseError:
    def test_check_parse_error(self, tmp_path: Path) -> None:
        """flowstate check <invalid.flow> exits 1 with parse error message."""
        flow_file = _write_flow(tmp_path, INVALID_FLOW_SOURCE)
        result = runner.invoke(app, ["check", str(flow_file)])
        assert result.exit_code == 1
        assert "Parse error" in result.output


class TestCheckTypeError:
    def test_check_type_error(self, tmp_path: Path) -> None:
        """flowstate check with type errors exits 1 and prints each error."""
        flow_file = _write_flow(tmp_path, VALID_FLOW_SOURCE)

        mock_errors = [
            MagicMock(rule="S1", message="No entry node", location=""),
            MagicMock(rule="S2", message="No exit node", location=""),
        ]

        with patch("flowstate.dsl.type_checker.check_flow", return_value=mock_errors):
            result = runner.invoke(app, ["check", str(flow_file)])

        assert result.exit_code == 1
        assert "Type error" in result.output


class TestCheckFileNotFound:
    def test_check_file_not_found(self) -> None:
        """flowstate check nonexistent.flow exits 1 with 'File not found'."""
        result = runner.invoke(app, ["check", "nonexistent.flow"])
        assert result.exit_code == 1
        assert "File not found" in result.output


# ---------------------------------------------------------------------------
# 6-7. server command
# ---------------------------------------------------------------------------


class TestServerCommand:
    def test_server_command_starts(self) -> None:
        """flowstate server invokes uvicorn.run with default host and port."""
        with patch("uvicorn.run") as mock_uvicorn:
            result = runner.invoke(app, ["server"])

        assert result.exit_code == 0
        mock_uvicorn.assert_called_once()
        call_kwargs = mock_uvicorn.call_args
        assert call_kwargs[1]["host"] == "127.0.0.1"
        assert call_kwargs[1]["port"] == 8080

    def test_server_custom_port(self) -> None:
        """flowstate server --port 9090 passes port 9090 to uvicorn."""
        with patch("uvicorn.run") as mock_uvicorn:
            result = runner.invoke(app, ["server", "--port", "9090"])

        assert result.exit_code == 0
        mock_uvicorn.assert_called_once()
        call_kwargs = mock_uvicorn.call_args
        assert call_kwargs[1]["port"] == 9090

    def test_server_custom_host(self) -> None:
        """flowstate server --host 0.0.0.0 passes the host to uvicorn."""
        with patch("uvicorn.run") as mock_uvicorn:
            result = runner.invoke(app, ["server", "--host", "0.0.0.0"])

        assert result.exit_code == 0
        call_kwargs = mock_uvicorn.call_args
        assert call_kwargs[1]["host"] == "0.0.0.0"

    def test_server_prints_startup_message(self) -> None:
        """flowstate server prints the startup message with host and port."""
        with patch("uvicorn.run"):
            result = runner.invoke(app, ["server"])

        assert "Starting Flowstate server on 127.0.0.1:8080" in result.output


# ---------------------------------------------------------------------------
# 8-10. runs command
# ---------------------------------------------------------------------------


class TestRunsEmpty:
    def test_runs_empty(self) -> None:
        """flowstate runs with no runs prints 'No runs found.'"""
        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.list_flow_runs.return_value = []
            mock_db.close.return_value = None
            result = runner.invoke(app, ["runs"])

        assert result.exit_code == 0
        assert "No runs found." in result.output


class TestRunsWithResults:
    def test_runs_with_results(self) -> None:
        """flowstate runs with results prints a formatted table."""
        mock_run1 = MagicMock()
        mock_run1.id = "aaaaaaaa-1111-2222-3333-444444444444"
        mock_run1.flow_definition_id = "def-1"
        mock_run1.status = "running"
        mock_run1.started_at = "2024-01-01T00:00:00"
        mock_run1.created_at = "2024-01-01T00:00:00"

        mock_run2 = MagicMock()
        mock_run2.id = "bbbbbbbb-1111-2222-3333-444444444444"
        mock_run2.flow_definition_id = "def-2"
        mock_run2.status = "completed"
        mock_run2.started_at = "2024-01-02T00:00:00"
        mock_run2.created_at = "2024-01-02T00:00:00"

        mock_def1 = MagicMock()
        mock_def1.id = "def-1"
        mock_def1.name = "my_flow"

        mock_def2 = MagicMock()
        mock_def2.id = "def-2"
        mock_def2.name = "other_flow"

        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.list_flow_runs.return_value = [mock_run1, mock_run2]
            mock_db.list_flow_definitions.return_value = [mock_def1, mock_def2]
            mock_db.close.return_value = None
            result = runner.invoke(app, ["runs"])

        assert result.exit_code == 0
        assert "aaaaaaaa..." in result.output
        assert "bbbbbbbb..." in result.output
        assert "my_flow" in result.output
        assert "other_flow" in result.output
        assert "running" in result.output
        assert "completed" in result.output


class TestRunsFilterByStatus:
    def test_runs_filter_by_status(self) -> None:
        """flowstate runs --status running passes status to the DB query."""
        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.list_flow_runs.return_value = []
            mock_db.close.return_value = None
            result = runner.invoke(app, ["runs", "--status", "running"])

        assert result.exit_code == 0
        mock_db.list_flow_runs.assert_called_once_with(status="running")


# ---------------------------------------------------------------------------
# 11-13. status command
# ---------------------------------------------------------------------------


class TestStatusByFullId:
    def test_status_by_full_id(self) -> None:
        """flowstate status <full-uuid> shows run details and tasks."""
        run_id = "aaaaaaaa-1111-2222-3333-444444444444"

        mock_run = MagicMock()
        mock_run.id = run_id
        mock_run.flow_definition_id = "def-1"
        mock_run.status = "running"
        mock_run.elapsed_seconds = 42.5
        mock_run.budget_seconds = 1800

        mock_def = MagicMock()
        mock_def.name = "my_flow"

        mock_task = MagicMock()
        mock_task.node_name = "scaffold"
        mock_task.generation = 1
        mock_task.status = "completed"

        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.get_flow_run.return_value = mock_run
            mock_db.get_flow_definition.return_value = mock_def
            mock_db.list_task_executions.return_value = [mock_task]
            mock_db.close.return_value = None
            result = runner.invoke(app, ["status", run_id])

        assert result.exit_code == 0
        assert run_id in result.output
        assert "my_flow" in result.output
        assert "running" in result.output
        assert "42.5s" in result.output
        assert "1800s" in result.output
        assert "scaffold (gen 1): completed" in result.output


class TestStatusByPrefix:
    def test_status_by_prefix(self) -> None:
        """flowstate status <prefix> shows the matching run."""
        full_id = "aaaaaaaa-1111-2222-3333-444444444444"

        mock_run = MagicMock()
        mock_run.id = full_id
        mock_run.flow_definition_id = "def-1"
        mock_run.status = "completed"
        mock_run.elapsed_seconds = 100.0
        mock_run.budget_seconds = 3600

        mock_def = MagicMock()
        mock_def.name = "prefix_flow"

        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            # Exact match fails
            mock_db.get_flow_run.return_value = None
            # Prefix match returns one run
            mock_db.list_flow_runs.return_value = [mock_run]
            mock_db.get_flow_definition.return_value = mock_def
            mock_db.list_task_executions.return_value = []
            mock_db.close.return_value = None
            result = runner.invoke(app, ["status", "aaaaaaaa"])

        assert result.exit_code == 0
        assert full_id in result.output
        assert "prefix_flow" in result.output


class TestStatusAmbiguousPrefix:
    def test_status_ambiguous_prefix(self) -> None:
        """flowstate status with ambiguous prefix exits 1 and lists matches."""
        run1 = MagicMock()
        run1.id = "aaa11111-1111-2222-3333-444444444444"

        run2 = MagicMock()
        run2.id = "aaa22222-1111-2222-3333-444444444444"

        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.get_flow_run.return_value = None
            mock_db.list_flow_runs.return_value = [run1, run2]
            mock_db.close.return_value = None
            result = runner.invoke(app, ["status", "aaa"])

        assert result.exit_code == 1
        assert "Ambiguous" in result.output
        assert run1.id in result.output
        assert run2.id in result.output


class TestStatusNotFound:
    def test_status_not_found(self) -> None:
        """flowstate status with unknown run ID exits 1."""
        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.get_flow_run.return_value = None
            mock_db.list_flow_runs.return_value = []
            mock_db.close.return_value = None
            result = runner.invoke(app, ["status", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# 14. run command with params
# ---------------------------------------------------------------------------


class TestRunWithParams:
    def test_run_with_params(self, tmp_path: Path) -> None:
        """flowstate run with --param flags correctly parses key=value pairs."""
        flow_file = _write_flow(tmp_path, VALID_FLOW_SOURCE)

        mock_run_id = "test-run-id-1234"

        with (
            patch("flowstate.state.repository.FlowstateDB") as MockDB,
        ):
            mock_db = MockDB.return_value
            mock_db.create_flow_definition.return_value = "def-id"
            mock_db.create_flow_run.return_value = mock_run_id
            mock_db.close.return_value = None

            result = runner.invoke(
                app,
                ["run", str(flow_file), "--param", "focus=auth", "--param", "depth=3"],
            )

        assert result.exit_code == 0
        assert mock_run_id in result.output

        # Verify create_flow_run was called
        mock_db.create_flow_run.assert_called_once()

    def test_run_param_with_equals_in_value(self, tmp_path: Path) -> None:
        """flowstate run --param query=a=b correctly uses partition."""
        flow_file = _write_flow(tmp_path, VALID_FLOW_SOURCE)

        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.create_flow_definition.return_value = "def-id"
            mock_db.create_flow_run.return_value = "run-id"
            mock_db.close.return_value = None

            result = runner.invoke(
                app,
                ["run", str(flow_file), "--param", "query=a=b"],
            )

        assert result.exit_code == 0

    def test_run_invalid_param_format(self, tmp_path: Path) -> None:
        """flowstate run --param badformat exits 1 with error."""
        flow_file = _write_flow(tmp_path, VALID_FLOW_SOURCE)
        result = runner.invoke(app, ["run", str(flow_file), "--param", "noequalssign"])
        assert result.exit_code == 1
        assert "Invalid param format" in result.output

    def test_run_file_not_found(self) -> None:
        """flowstate run nonexistent.flow exits 1."""
        result = runner.invoke(app, ["run", "nonexistent.flow"])
        assert result.exit_code == 1
        assert "File not found" in result.output


# ---------------------------------------------------------------------------
# schedules command
# ---------------------------------------------------------------------------


class TestSchedulesEmpty:
    def test_schedules_empty(self) -> None:
        """flowstate schedules with no schedules prints 'No schedules found.'"""
        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.list_flow_schedules.return_value = []
            mock_db.close.return_value = None
            result = runner.invoke(app, ["schedules"])

        assert result.exit_code == 0
        assert "No schedules found." in result.output


class TestSchedulesWithResults:
    def test_schedules_with_results(self) -> None:
        """flowstate schedules lists schedules in a table."""
        mock_sched = MagicMock()
        mock_sched.flow_definition_id = "def-1"
        mock_sched.cron_expression = "0 */6 * * *"
        mock_sched.enabled = 1
        mock_sched.next_trigger_at = "2024-01-15T06:00:00"

        mock_def = MagicMock()
        mock_def.id = "def-1"
        mock_def.name = "my_scheduled_flow"

        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.list_flow_schedules.return_value = [mock_sched]
            mock_db.list_flow_definitions.return_value = [mock_def]
            mock_db.close.return_value = None
            result = runner.invoke(app, ["schedules"])

        assert result.exit_code == 0
        assert "my_scheduled_flow" in result.output
        assert "0 */6 * * *" in result.output
        assert "enabled" in result.output


# ---------------------------------------------------------------------------
# trigger command
# ---------------------------------------------------------------------------


class TestTrigger:
    def test_trigger_success(self) -> None:
        """flowstate trigger <flow-name> creates a run and prints the ID."""
        mock_def = MagicMock()
        mock_def.id = "def-1"
        mock_def.name = "my_flow"

        mock_sched = MagicMock()
        mock_sched.flow_definition_id = "def-1"

        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.get_flow_definition_by_name.return_value = mock_def
            mock_db.list_flow_schedules.return_value = [mock_sched]
            mock_db.create_flow_run.return_value = "new-run-id"
            mock_db.close.return_value = None
            result = runner.invoke(app, ["trigger", "my_flow"])

        assert result.exit_code == 0
        assert "Triggered: new-run-id" in result.output

    def test_trigger_no_flow_found(self) -> None:
        """flowstate trigger unknown_flow exits 1 with error."""
        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.get_flow_definition_by_name.return_value = None
            mock_db.close.return_value = None
            result = runner.invoke(app, ["trigger", "unknown_flow"])

        assert result.exit_code == 1
        assert "No flow found" in result.output

    def test_trigger_no_schedule(self) -> None:
        """flowstate trigger for a flow with no schedule exits 1 with error."""
        mock_def = MagicMock()
        mock_def.id = "def-1"
        mock_def.name = "unscheduled_flow"

        with patch("flowstate.state.repository.FlowstateDB") as MockDB:
            mock_db = MockDB.return_value
            mock_db.get_flow_definition_by_name.return_value = mock_def
            mock_db.list_flow_schedules.return_value = []
            mock_db.close.return_value = None
            result = runner.invoke(app, ["trigger", "unscheduled_flow"])

        assert result.exit_code == 1
        assert "No schedule found" in result.output
