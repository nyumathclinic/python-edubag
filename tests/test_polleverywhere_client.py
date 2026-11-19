#!/usr/bin/env python
"""Tests for polleverywhere client module."""

import inspect
from datetime import date, datetime

import pytest

from edubag.polleverywhere.client import (
    Assignment,
    Class,
    Client,
    Poll,
    _download_reports,
    _flatten_folders,
    _folder_subtree,
    _JsonApi,
    _parse_timestamp,
    _poll_from_api,
    _ReportJob,
    _resolve_folder,
    _run_sync_in_thread,
)


class TestRunSyncInThread:
    """Test the _run_sync_in_thread helper function."""

    def test_returns_function_result(self):
        assert _run_sync_in_thread(lambda x, y: x + y, 2, 3) == 5

    def test_propagates_exceptions(self):
        def _raises():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _run_sync_in_thread(_raises)


class TestParseTimestamp:
    """Test the _parse_timestamp helper function."""

    def test_parses_valid_timestamp(self):
        assert _parse_timestamp("09/29/26 04:03 PM") == datetime(2026, 9, 29, 16, 3)

    def test_parses_am_timestamp(self):
        assert _parse_timestamp("09/11/26 09:47 AM") == datetime(2026, 9, 11, 9, 47)

    def test_returns_none_for_none(self):
        assert _parse_timestamp(None) is None

    def test_returns_none_for_empty_string(self):
        assert _parse_timestamp("") is None
        assert _parse_timestamp("   ") is None

    def test_returns_none_for_unparseable_text(self):
        assert _parse_timestamp("Never") is None


class TestClassDataclass:
    """Test the Class dataclass."""

    def test_defaults(self):
        cls = Class(id=1, name="MATH-UA 120 Discrete Mathematics")
        assert cls.id == 1
        assert cls.name == "MATH-UA 120 Discrete Mathematics"
        assert cls.start is None
        assert cls.end is None
        assert cls.last_roster_sync is None

    def test_all_fields(self):
        cls = Class(
            id=62690,
            name="MATH-UA 122.016+021 Calculus II, Fall 2026",
            start=date(2026, 9, 2),
            end=date(2026, 12, 28),
            last_roster_sync=datetime(2026, 9, 29, 16, 3),
        )
        assert cls.start == date(2026, 9, 2)
        assert cls.end == date(2026, 12, 28)
        assert cls.last_roster_sync == datetime(2026, 9, 29, 16, 3)


class TestAssignmentDataclass:
    """Test the Assignment dataclass."""

    def test_defaults(self):
        assignment = Assignment(id=54952, name="2026-09-03 Polls")
        assert assignment.id == 54952
        assert assignment.name == "2026-09-03 Polls"
        assert assignment.last_grade_sync is None
        assert assignment.class_id is None

    def test_all_fields(self):
        assignment = Assignment(
            id=54952,
            name="2026-09-03 Polls",
            last_grade_sync=datetime(2026, 9, 11, 9, 47),
            class_id=62690,
        )
        assert assignment.last_grade_sync == datetime(2026, 9, 11, 9, 47)
        assert assignment.class_id == 62690


class TestClientUrls:
    """Test URL construction on the Client."""

    def test_default_base_url(self):
        assert Client.base_url == "https://www.polleverywhere.com"

    def test_courses_url(self):
        client = Client(auth_state_path="/tmp/polleverywhere_auth_test.json")
        assert client.courses_url == "https://www.polleverywhere.com/lms/lti_advantage/user_connections"

    def test_class_url(self):
        client = Client(auth_state_path="/tmp/polleverywhere_auth_test.json")
        assert client._class_url(62690) == (
            "https://www.polleverywhere.com/lms/lti_advantage/user_connections/62690"
        )

    def test_custom_base_url(self):
        client = Client(base_url="https://example.test", auth_state_path="/tmp/polleverywhere_auth_test.json")
        assert client.courses_url == "https://example.test/lms/lti_advantage/user_connections"
        assert client._class_url(1) == "https://example.test/lms/lti_advantage/user_connections/1"


class TestClientMethodsExist:
    """Test that the Client exposes the methods described in the issue."""

    def test_fetch_methods_exist(self):
        assert callable(Client.fetch_class)
        assert callable(Client.fetch_assignment)

    def test_sync_methods_exist(self):
        assert callable(Client.sync_roster_to_lms)
        assert callable(Client.sync_assignment_to_lms)
        assert callable(Client.sync_all_assignments_to_lms)

    def test_fetch_class_signature(self):
        params = list(inspect.signature(Client.fetch_class).parameters.keys())
        assert "self" in params
        assert "id" in params

    def test_sync_roster_to_lms_signature(self):
        params = list(inspect.signature(Client.sync_roster_to_lms).parameters.keys())
        assert "cls" in params

    def test_sync_assignment_to_lms_signature(self):
        params = list(inspect.signature(Client.sync_assignment_to_lms).parameters.keys())
        assert "assignment" in params

    def test_sync_all_assignments_to_lms_signature(self):
        params = list(inspect.signature(Client.sync_all_assignments_to_lms).parameters.keys())
        assert "cls" in params


class TestSyncAssignmentToLmsValidation:
    """Test that sync_assignment_to_lms requires a class id one way or another."""

    def test_raises_without_class_id(self):
        client = Client(auth_state_path="/tmp/polleverywhere_auth_test.json")
        assignment = Assignment(id=1, name="Quiz 1")
        try:
            client.sync_assignment_to_lms(assignment)
        except ValueError as error:
            assert "class_id" in str(error)
        else:
            raise AssertionError("Expected missing class_id to raise ValueError")

    def test_does_not_raise_value_error_with_class_id_argument(self):
        # Should get past the class_id validation and attempt to launch a browser,
        # which will fail in this sandboxed/headless-less test environment with
        # something other than a ValueError (Playwright browsers aren't installed).
        client = Client(auth_state_path="/tmp/polleverywhere_auth_test.json")
        assignment = Assignment(id=1, name="Quiz 1")
        with pytest.raises(Exception) as excinfo:
            client.sync_assignment_to_lms(assignment, class_id=123)
        assert not isinstance(excinfo.value, ValueError)

    def test_does_not_raise_value_error_with_assignment_class_id(self):
        client = Client(auth_state_path="/tmp/polleverywhere_auth_test.json")
        assignment = Assignment(id=1, name="Quiz 1", class_id=123)
        with pytest.raises(Exception) as excinfo:
            client.sync_assignment_to_lms(assignment)
        assert not isinstance(excinfo.value, ValueError)


GROUPS = [
    {
        "id": 1,
        "name": "1268 120 Discrete Fall 2026",
        "children": [
            {"id": 2, "name": "Week 1", "children": [{"id": 4, "name": "Extra"}]},
            {"id": 3, "name": "Week 2", "children": []},
        ],
    },
    {"id": 5, "name": "1264 120 Discrete Spring 2026", "children": [{"id": 6, "name": "Week 1"}]},
]


class TestPollFromApi:
    def test_flat_activity(self):
        poll = _poll_from_api(
            {
                "id": 91866097,
                "type": "multiple_choice_poll",
                "title": " How much sleep did you get last night? ",
                "group_id": 22158726,
                "permalink": "I5rDWIL716Xw7GCWCwyux",
                "results_count": 73,
                "created_at": "2026-09-02T16:16:45-05:00",
                "updated_at": "2026-09-03T13:03:11-05:00",
                "state": "opened",
                "owner_id": 14524383,
            }
        )
        assert poll.id == 91866097
        assert poll.type == "multiple_choice_poll"
        assert poll.title == "How much sleep did you get last night?"
        assert poll.group_id == 22158726
        assert poll.results_count == 73
        assert poll.updated_at.isoformat() == "2026-09-03T13:03:11-05:00"
        assert poll.owner_id == 14524383

    @pytest.mark.parametrize("kind", ["multiple_choice_poll", "free_text_poll", "discourse"])
    def test_wrapped_activity(self, kind):
        poll = _poll_from_api({kind: {"id": 7, "title": "Q", "group_id": 3, "results_count": 0}})
        assert poll.type == kind
        assert (poll.id, poll.group_id, poll.results_count) == (7, 3, 0)

    def test_hashable(self):
        poll = _poll_from_api({"id": 7, "type": "free_text_poll"})
        assert {poll: "x"}[poll] == "x"

    def test_group_object_and_missing_fields(self):
        poll = _poll_from_api({"id": "8", "type": "free_text_poll", "group": {"id": 3}})
        assert poll.id == 8
        assert poll.group_id == 3
        assert poll.title == ""
        assert poll.updated_at is None


class TestFolders:
    def test_flatten_paths_and_parents(self):
        folders = _flatten_folders(GROUPS)
        assert [f.id for f in folders] == [1, 2, 4, 3, 5, 6]
        by_id = {f.id: f for f in folders}
        assert by_id[4].path == "1268 120 Discrete Fall 2026/Week 1/Extra"
        assert by_id[4].parent_id == 2
        assert by_id[1].parent_id is None

    def test_subtree(self):
        folders = _flatten_folders(GROUPS)
        assert _folder_subtree(folders, 1) == [1, 2, 4, 3]
        assert _folder_subtree(folders, 3) == [3]

    def test_resolve_by_id_name_and_path(self):
        folders = _flatten_folders(GROUPS)
        assert _resolve_folder(folders, 3).id == 3
        assert _resolve_folder(folders, "3").id == 3
        assert _resolve_folder(folders, "1268 120 discrete fall 2026").id == 1
        assert _resolve_folder(folders, "1264 120 Discrete Spring 2026/Week 1").id == 6

    def test_resolve_ambiguous_name(self):
        with pytest.raises(ValueError, match="ambiguous"):
            _resolve_folder(_flatten_folders(GROUPS), "Week 1")

    def test_resolve_missing(self):
        with pytest.raises(ValueError, match="No Poll Everywhere folder"):
            _resolve_folder(_flatten_folders(GROUPS), "Nope")


class FakeResponse:
    def __init__(self, status=200, payload=None, body=b"", url="https://www.polleverywhere.com/api"):
        self.status, self._payload, self._body, self.url = status, payload, body, url

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        return self._payload

    def body(self):
        return self._body

    def text(self):
        return str(self._payload)


class FakeRequest:
    """Stand-in for a Playwright APIRequestContext serving the reports API."""

    def __init__(self, states=("queued", "finished"), csv_status=200, login_redirect=False):
        self.calls: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self.states = list(states)
        self.csv_status = csv_status
        self.login_redirect = login_redirect
        self.next_id = 100

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        if self.login_redirect:
            return FakeResponse(url="https://id.polleverywhere.com/login", payload={})
        if path == "/api/csrf_token":
            return FakeResponse(payload={"token": "tok"})
        if path.endswith(".csv"):
            return FakeResponse(status=self.csv_status, body=b"First name,Last name\nA,B\n")
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return FakeResponse(payload={"id": int(path.rsplit("/", 1)[1]), "state": state})

    def post(self, path, headers=None, data=None):
        self.calls.append(("POST", path))
        assert headers["X-CSRF-Token"] == "tok"
        self.created.append(__import__("json").loads(data))
        self.next_id += 1
        return FakeResponse(status=202, payload={"id": self.next_id, "state": "queued"})

    def delete(self, path, headers=None):
        self.calls.append(("DELETE", path))
        return FakeResponse(status=204)


def _run(request, jobs, **kwargs):
    _download_reports(_JsonApi(request), jobs, sleep=lambda s: None, **kwargs)


class TestDownloadReports:
    def test_create_poll_download_delete(self, tmp_path):
        request = FakeRequest()
        poll = Poll(id=91866150, type="discourse", title="Q", owner_id=14524383)
        job = _ReportJob(poll=poll, dest=tmp_path / "91866150.csv")
        _run(request, [job])
        assert job.error is None
        assert job.dest.read_bytes().startswith(b"First name")
        assert request.created[0]["polls"] == [91866150]
        assert request.created[0]["report_type"] == "participant"
        assert request.created[0]["filters"] == {"archive_group_id": -1, "presenter_ids": [14524383]}
        order = [c for c in request.calls if c[1] != "/api/csrf_token"]
        assert order == [
            ("POST", "/api/reports"),
            ("GET", "/api/reports/101"),
            ("GET", "/api/reports/101"),
            ("GET", "/api/reports/101.csv"),
            ("DELETE", "/api/reports/101"),
        ]

    def test_current_run_and_plain_ids(self, tmp_path):
        request = FakeRequest(states=("finished",))
        job = _ReportJob(poll=5, dest=tmp_path / "5.csv")
        _run(request, [job], runs="current")
        assert request.created[0]["filters"] == {"archive_group_id": 0}

    def test_keep_reports(self, tmp_path):
        request = FakeRequest(states=("finished",))
        _run(request, [_ReportJob(poll=5, dest=tmp_path / "5.csv")], keep_reports=True)
        assert not [c for c in request.calls if c[0] == "DELETE"]

    def test_failed_download_is_recorded_and_report_deleted(self, tmp_path):
        request = FakeRequest(states=("finished",), csv_status=500)
        jobs = [_ReportJob(poll=5, dest=tmp_path / "5.csv"), _ReportJob(poll=6, dest=tmp_path / "6.csv")]
        _run(request, jobs)
        assert all(isinstance(job.error, RuntimeError) for job in jobs)
        assert sorted(c[1] for c in request.calls if c[0] == "DELETE") == [
            "/api/reports/101",
            "/api/reports/102",
        ]
        assert not (tmp_path / "5.csv").exists()

    def test_failed_report_state(self, tmp_path):
        request = FakeRequest(states=("failed",))
        job = _ReportJob(poll=5, dest=tmp_path / "5.csv")
        _run(request, [job])
        assert "failed" in str(job.error)

    def test_timeout(self, tmp_path):
        request = FakeRequest(states=("queued",))
        ticks = iter(range(0, 10000, 100))
        job = _ReportJob(poll=5, dest=tmp_path / "5.csv")
        _download_reports(
            _JsonApi(request), [job], timeout=250, sleep=lambda s: None, clock=lambda: next(ticks)
        )
        assert "after 250s" in str(job.error)
        assert ("DELETE", "/api/reports/101") in request.calls

    def test_batches(self, tmp_path):
        request = FakeRequest(states=("finished",))
        jobs = [_ReportJob(poll=i, dest=tmp_path / f"{i}.csv") for i in range(5)]
        _run(request, jobs, batch_size=2)
        assert all(job.error is None for job in jobs)
        posts = [i for i, c in enumerate(request.calls) if c[0] == "POST"]
        first_csv = next(i for i, c in enumerate(request.calls) if c[1].endswith(".csv"))
        assert sum(1 for i in posts if i < first_csv) == 2

    def test_auth_expired_raises(self, tmp_path):
        request = FakeRequest(login_redirect=True)
        with pytest.raises(RuntimeError, match="Authentication session expired"):
            _run(request, [_ReportJob(poll=5, dest=tmp_path / "5.csv")])

    def test_bad_runs(self, tmp_path):
        with pytest.raises(ValueError, match="runs"):
            _run(FakeRequest(), [_ReportJob(poll=5, dest=tmp_path / "5.csv")], runs="some")


class TestClientJsonApiGuards:
    def test_missing_auth_state(self, tmp_path):
        client = Client(auth_state_path=tmp_path / "missing.json")
        with pytest.raises(RuntimeError, match="authenticate"):
            client.list_folders()

    def test_download_responses_validates_runs(self, tmp_path):
        client = Client(auth_state_path=tmp_path / "missing.json")
        with pytest.raises(ValueError, match="runs"):
            client.download_responses({5: tmp_path / "5.csv"}, runs="some")

    def test_download_responses_nothing_to_do(self, tmp_path):
        client = Client(auth_state_path=tmp_path / "missing.json")
        assert client.download_responses({}) == {}
