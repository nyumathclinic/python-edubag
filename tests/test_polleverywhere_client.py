#!/usr/bin/env python
"""Tests for polleverywhere client module."""

import inspect
from datetime import date, datetime

from edubag.polleverywhere.client import Assignment, Class, Client, _parse_timestamp


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

    def test_does_not_raise_value_error_with_class_id_argument(self, monkeypatch):
        # Should get past the class_id validation and attempt to launch a browser,
        # which will fail in this sandboxed/headless-less test environment with
        # something other than a ValueError.
        client = Client(auth_state_path="/tmp/polleverywhere_auth_test.json")
        assignment = Assignment(id=1, name="Quiz 1")
        try:
            client.sync_assignment_to_lms(assignment, class_id=123)
        except ValueError:
            raise AssertionError("Did not expect a ValueError when class_id is provided") from None
        except Exception:
            pass

    def test_does_not_raise_value_error_with_assignment_class_id(self):
        client = Client(auth_state_path="/tmp/polleverywhere_auth_test.json")
        assignment = Assignment(id=1, name="Quiz 1", class_id=123)
        try:
            client.sync_assignment_to_lms(assignment)
        except ValueError:
            raise AssertionError("Did not expect a ValueError when assignment.class_id is set") from None
        except Exception:
            pass
