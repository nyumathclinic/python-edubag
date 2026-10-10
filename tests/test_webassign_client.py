"""Tests for the WebAssign client module (no network)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from edubag.webassign import app
from edubag.webassign import client as wa
from edubag.webassign.client import WebAssignAuthError, WebAssignClient


def test_client_initialization_defaults():
    client = WebAssignClient()
    assert client.base_url == "https://www.webassign.net"
    assert client.auth_state_path.name == "webassign_auth.json"


def test_client_initialization_overrides(tmp_path: Path):
    client = WebAssignClient(base_url="https://example.test/", auth_state_path=tmp_path / "auth.json")
    assert client.base_url == "https://example.test"
    assert client.auth_state_path == tmp_path / "auth.json"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.webassign.net/v4cgi/faculty.pl?action=", True),
        ("https://www.webassign.net/wa-auth/login", False),
        ("https://www.webassign.net/", False),
        ("https://account.cengage.com/login", False),
    ],
)
def test_is_signed_in_url(url: str, expected: bool):
    assert WebAssignClient()._is_signed_in_url(url) is expected


def test_file_type_value():
    assert wa.file_type_value("csv") == "csv"
    assert wa.file_type_value("TSV") == "tsv"
    assert wa.file_type_value("excel") == "xls"
    with pytest.raises(ValueError, match="Unknown file type"):
        wa.file_type_value("pdf")


def test_student_filter_value():
    assert wa.student_filter_value("current") == "current_students"
    assert wa.student_filter_value("dropped") == "dropped_students"
    assert wa.student_filter_value("All") == "all"
    with pytest.raises(ValueError, match="Unknown student filter"):
        wa.student_filter_value("enrolled")


def test_assignment_filter_value():
    assert wa.assignment_filter_value("all") == "past_current_future"
    assert wa.assignment_filter_value("past-current") == "past_current"
    assert wa.assignment_filter_value("Past") == "past"
    with pytest.raises(ValueError, match="Unknown assignment filter"):
        wa.assignment_filter_value("recent")


def test_section_id():
    assert wa.section_id("1234567") == "1234567"
    assert wa.section_id(1234567) == "1234567"
    assert wa.section_id("7654321,1234567") == "1234567"
    with pytest.raises(ValueError, match="Invalid WebAssign section ID"):
        wa.section_id("MATH-UA 122")

def test_sync_message():
    assert "successfully" in wa.sync_message(200)
    assert "once per hour" in wa.sync_message(429)
    assert wa.sync_message(500) == wa.SYNC_ERROR_MESSAGE


def test_invalid_options_rejected_before_browser(tmp_path: Path):
    client = WebAssignClient(auth_state_path=tmp_path / "auth.json")
    (tmp_path / "auth.json").write_text("{}")
    with pytest.raises(ValueError, match="Unknown file type"):
        client.save_roster(["1234567"], file_type="pdf")
    with pytest.raises(ValueError, match="Unknown student filter"):
        client.save_roster(["1234567"], students="enrolled")
    with pytest.raises(ValueError, match="Unknown assignment filter"):
        client.save_scores(["1234567"], assignments="recent")
    with pytest.raises(ValueError, match="At least one section"):
        client.save_gradebook([])


def test_reauth_retries_once_on_auth_error(tmp_path: Path, monkeypatch):
    client = WebAssignClient(auth_state_path=tmp_path / "auth.json")
    (tmp_path / "auth.json").write_text("{}")
    auth_calls = []
    monkeypatch.setattr(client, "authenticate", lambda headless=False: auth_calls.append(headless))
    attempts = []

    def operation():
        attempts.append(1)
        if len(attempts) == 1:
            raise WebAssignAuthError("expired")
        return "done"

    assert client._run_with_reauth(operation, headless=True) == "done"
    assert len(attempts) == 2
    assert auth_calls == [True]


def test_reauth_does_not_retry_other_errors(tmp_path: Path, monkeypatch):
    client = WebAssignClient(auth_state_path=tmp_path / "auth.json")
    (tmp_path / "auth.json").write_text("{}")
    monkeypatch.setattr(client, "authenticate", lambda headless=False: pytest.fail("should not re-authenticate"))

    def operation():
        raise RuntimeError("no link")

    with pytest.raises(RuntimeError, match="no link"):
        client._run_with_reauth(operation, headless=True)


def test_sync_scores_not_repeated_once_sent(tmp_path: Path, monkeypatch):
    """A completed sync recorded in the session state is returned, not re-sent."""
    client = WebAssignClient(auth_state_path=tmp_path / "auth.json")
    (tmp_path / "auth.json").write_text("{}")
    monkeypatch.setattr(wa, "sync_playwright", lambda: pytest.fail("browser should not launch"))
    done = {"section": "1234567", "sent": True, "dry_run": False, "status": 200, "message": "ok"}
    assert client._sync_scores_session("1234567", False, True, {"result": done}) == done


@pytest.mark.parametrize(
    "command", ["authenticate", "list-sections", "save-roster", "save-scores", "save-gradebook", "sync-scores"]
)
def test_cli_help(command: str):
    result = CliRunner().invoke(app, ["client", command, "--help"])
    assert result.exit_code == 0
