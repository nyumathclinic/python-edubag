#!/usr/bin/env python
"""Tests for albert client module."""

import inspect
from urllib.parse import parse_qs, urlparse

from edubag.albert.client import AlbertClient, _normalize_label


class TestNormalizeLabel:
    """Test the _normalize_label helper function."""

    def test_basic_label(self):
        """Test basic label normalization."""
        assert _normalize_label("Class Number") == "class_number"

    def test_multiple_words(self):
        """Test label with multiple words."""
        assert _normalize_label("Full Class Detail") == "full_class_detail"

    def test_special_characters(self):
        """Test label with special characters."""
        assert _normalize_label("Class Number (Test)") == "class_number_test"

    def test_leading_trailing_spaces(self):
        """Test label with leading/trailing spaces."""
        assert _normalize_label("  Class Number  ") == "class_number"

    def test_multiple_spaces(self):
        """Test label with multiple spaces."""
        assert _normalize_label("Class  Number  Detail") == "class_number_detail"

    def test_all_lowercase(self):
        """Test label that's already lowercase."""
        assert _normalize_label("class number") == "class_number"

    def test_mixed_case(self):
        """Test label with mixed case."""
        assert _normalize_label("ClassNumber") == "classnumber"


class TestAlbertClientMarkEngaged:
    """Test the mark_engaged method exists and has the correct signature."""

    def test_mark_engaged_method_exists(self):
        """Test that mark_engaged method exists on AlbertClient."""
        assert hasattr(AlbertClient, "mark_engaged")
        assert callable(AlbertClient.mark_engaged)

    def test_mark_engaged_signature(self):
        """Test that mark_engaged has the expected parameters."""
        method = AlbertClient.mark_engaged
        sig = inspect.signature(method)
        params = list(sig.parameters.keys())

        # Check required parameters are present
        assert "self" in params
        assert "class_number" in params
        assert "term" in params
        assert "email_addresses" in params

        # Check optional parameters are present
        assert "username" in params
        assert "password" in params
        assert "headless" in params

    def test_private_methods_exist(self):
        """Test that the private helper methods exist."""
        assert hasattr(AlbertClient, "_find_academic_engagement_link")
        assert hasattr(AlbertClient, "_mark_engaged_session")


class TestAlbertClientDirectCourseUrl:
    """Test construction of direct Albert course URLs."""

    def test_course_url_uses_term_code_and_all_required_parameters(self):
        url = AlbertClient._course_url(
            AlbertClient.course_base_url,
            class_number=12345,
            term="Fall 2025",
            instructor_id="abc 123",
        )

        assert url.startswith(AlbertClient.course_base_url + "?")
        assert parse_qs(urlparse(url).query) == {
            "Page": ["NYU_FACCLSRST_NUFL"],
            "Action": ["U"],
            "ExactKeys": ["Y"],
            "INSTRUCTOR_ID": ["abc 123"],
            "INSTITUTION": ["NYUNV"],
            "CLASS_NBR": ["12345"],
            "STRM": ["1258"],
        }

    def test_course_url_uses_environment_instructor_id(self, monkeypatch):
        monkeypatch.setenv("ALBERT_INSTRUCTOR_ID", "env-instructor")

        url = AlbertClient._course_url("https://example.test/course", 123, "Spring 2026")

        assert parse_qs(urlparse(url).query)["INSTRUCTOR_ID"] == ["env-instructor"]

    def test_course_url_accepts_integer_term_code(self):
        url = AlbertClient._course_url(
            AlbertClient.course_base_url,
            class_number=123,
            term=1258,
            instructor_id="instructor",
        )

        assert parse_qs(urlparse(url).query)["STRM"] == ["1258"]

    def test_course_url_accepts_string_term_code(self):
        url = AlbertClient._course_url(
            AlbertClient.course_base_url,
            class_number=123,
            term="1258",
            instructor_id="instructor",
        )

        assert parse_qs(urlparse(url).query)["STRM"] == ["1258"]

    def test_explicit_instructor_id_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("ALBERT_INSTRUCTOR_ID", "env-instructor")

        url = AlbertClient._course_url("https://example.test/course", 123, "Spring 2026", "explicit")

        assert parse_qs(urlparse(url).query)["INSTRUCTOR_ID"] == ["explicit"]

    def test_missing_instructor_id_is_an_error(self, monkeypatch):
        monkeypatch.delenv("ALBERT_INSTRUCTOR_ID", raising=False)

        try:
            AlbertClient._course_url("https://example.test/course", 123, "Spring 2026")
        except ValueError as error:
            assert "ALBERT_INSTRUCTOR_ID" in str(error)
        else:
            raise AssertionError("Expected missing instructor ID to raise ValueError")

    def test_direct_fetch_methods_exist(self):
        assert callable(AlbertClient.fetch_roster)
        assert callable(AlbertClient.fetch_course_details)


class TestReconcileStaleHeaderSection:
    """Test staleness correction for the roster header 'section' field.

    Regression coverage for a bug where fetching several classes in a row
    via direct URLs (same authenticated session) left the roster header's
    `section` field one fetch behind the class actually requested, causing
    the last class in a batch to be silently mislabeled and dropped by
    downstream `drop_duplicates(subset=["section"])` processing.
    """

    def test_corrects_stale_section_using_full_course_name(self):
        class_details = {
            "section": "016",
            "full_course_name": "MATH-UA 120 - 020 Discrete Mathematics",
        }
        AlbertClient._reconcile_stale_header_section(class_details)
        assert class_details["section"] == "020"

    def test_leaves_matching_section_untouched(self):
        class_details = {
            "section": "020",
            "full_course_name": "MATH-UA 120 - 020 Discrete Mathematics",
        }
        AlbertClient._reconcile_stale_header_section(class_details)
        assert class_details["section"] == "020"

    def test_leaves_section_untouched_without_full_course_name(self):
        class_details = {"section": "016"}
        AlbertClient._reconcile_stale_header_section(class_details)
        assert class_details["section"] == "016"

    def test_leaves_section_untouched_when_name_unparseable(self):
        class_details = {"section": "016", "full_course_name": "Discrete Mathematics"}
        AlbertClient._reconcile_stale_header_section(class_details)
        assert class_details["section"] == "016"
