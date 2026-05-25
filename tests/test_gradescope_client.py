#!/usr/bin/env python
"""Tests for gradescope client module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from dotenv import load_dotenv

load_dotenv()

from edubag.albert.term import Season, Term
from edubag.gradescope.client import GradescopeClient


class TestGradescopeClient:
    """Test the GradescopeClient class."""

    def test_client_initialization(self):
        """Test basic client initialization."""
        client = GradescopeClient()
        assert client.base_url == "https://gradescope.com"
        assert client.auth_state_path.name == "gradescope_auth.json"

    def test_client_custom_base_url(self):
        """Test client initialization with custom base URL."""
        custom_url = "https://custom.gradescope.com"
        client = GradescopeClient(base_url=custom_url)
        assert client.base_url == custom_url

    def test_client_custom_auth_path(self):
        """Test client initialization with custom auth state path."""
        custom_path = Path("/tmp/custom_auth.json")
        client = GradescopeClient(auth_state_path=custom_path)
        assert client.auth_state_path == custom_path

    def test_default_auth_state_path(self):
        """Test the default auth state path generation."""
        path = GradescopeClient._default_auth_state_path()
        assert path.name == "gradescope_auth.json"
        assert "edubag" in str(path)

    def test_extract_course_details(self):
        """Test extracting course details from a page."""
        client = GradescopeClient()

        # Mock page object
        mock_page = Mock()
        mock_page.url = "https://gradescope.com/courses/123456"

        # Mock course name element
        mock_course_number = Mock()
        mock_course_number.count.return_value = 1
        mock_course_number.text_content.return_value = "MATH-UA 122.006"
        
        # Setup locator side effects
        def locator_side_effect(selector):
            if selector == "h1.courseHeader--title":
                return mock_course_number
            return Mock(count=lambda: 0)

        mock_page.locator.side_effect = locator_side_effect
        mock_page.goto = Mock()
        mock_page.wait_for_load_state = Mock()

        details = client._extract_course_details(mock_page)
        assert "course_number" in details
        assert details["course_number"] == "MATH-UA 122.006"

    def test_extract_course_details_with_instructors(self):
        """Test extracting course details including instructors."""
        client = GradescopeClient()

        # Mock page object
        mock_page = Mock()
        mock_page.url = "https://gradescope.com/courses/123456"

        # Mock instructor list
        mock_instructor1 = Mock()
        mock_instructor1.get_attribute.return_value = "Instructor: John Doe"
        mock_instructor2 = Mock()
        mock_instructor2.get_attribute.return_value = "Instructor: Jane Smith"

        mock_instructor_list = Mock()
        mock_instructor_list.count.return_value = 2
        mock_instructor_list.all.return_value = [mock_instructor1, mock_instructor2]

        def locator_side_effect(selector):
            if selector == "li[aria-label^='Instructor:']":
                return mock_instructor_list
            return Mock(count=lambda: 0)

        mock_page.locator.side_effect = locator_side_effect
        mock_page.goto = Mock()
        mock_page.wait_for_load_state = Mock()

        details = client._extract_course_details(mock_page)
        assert "instructors" in details
        assert "John Doe" in details["instructors"]

    def test_fetch_class_details_with_term_object(self):
        """Test that fetch_class_details accepts Term objects."""
        client = GradescopeClient()
        term = Term(2025, Season.FALL)

        # Mock authentication state path to exist
        with patch("pathlib.Path.exists", return_value=True):
            # Mock the session method
            with patch.object(client, "_fetch_class_details_session", return_value=[]) as mock_session:
                result = client.fetch_class_details(course_name="Calculus II", term=term, headless=True)

                # Verify the session method was called with the term
                mock_session.assert_called_once_with("Calculus II", term, True)
                assert result == []

    def test_fetch_class_details_with_output(self):
        """Test that fetch_class_details saves to output file."""
        client = GradescopeClient()

        # Use tempfile for cross-platform compatibility
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_output.json"

            # Mock authentication state path to exist
            with patch("pathlib.Path.exists", return_value=True):
                # Mock the session method
                with patch.object(
                    client,
                    "_fetch_class_details_session",
                    return_value=[{"course_name": "Test Course"}],
                ):
                    # Mock file operations
                    with patch("builtins.open", create=True):
                        with patch("pathlib.Path.mkdir"):
                            result = client.fetch_class_details(
                                course_name="Test Course",
                                term="Fall 2025",
                                headless=True,
                                output=output_path,
                            )

                            assert result == [{"course_name": "Test Course"}]

    def test_save_roster_with_course_id(self):
        """Test save_roster constructs correct URL from course ID."""
        client = GradescopeClient()

        # Mock authentication state path to exist
        with patch("pathlib.Path.exists", return_value=True):
            # Mock the session method
            with patch.object(client, "_save_roster_session", return_value=Path("roster.csv")) as mock_session:
                result = client.save_roster(course="12345", headless=True)

                # Verify the session method was called with the course ID
                mock_session.assert_called_once_with("12345", None, True)
                assert result == [Path("roster.csv")]

    def test_save_roster_with_course_url(self):
        """Test save_roster handles full course URL."""
        client = GradescopeClient()

        # Mock authentication state path to exist
        with patch("pathlib.Path.exists", return_value=True):
            # Mock the session method
            with patch.object(
                client,
                "_save_roster_session",
                return_value=Path("roster.csv"),
            ) as mock_session:
                result = client.save_roster(course="https://gradescope.com/courses/12345", headless=True)

                # Verify the session method was called with the full URL
                mock_session.assert_called_once_with("https://gradescope.com/courses/12345", None, True)
                assert result == [Path("roster.csv")]

    def test_save_roster_with_save_dir(self):
        """Test save_roster with custom save directory."""
        client = GradescopeClient()

        # Use tempfile for cross-platform compatibility
        with tempfile.TemporaryDirectory() as tmpdir:
            save_dir = Path(tmpdir)

            # Mock authentication state path to exist
            with patch("pathlib.Path.exists", return_value=True):
                # Mock the session method
                with patch.object(
                    client,
                    "_save_roster_session",
                    return_value=save_dir / "roster.csv",
                ) as mock_session:
                    result = client.save_roster(course="12345", save_dir=save_dir, headless=True)

                    # Verify the session method was called with the save_dir
                    mock_session.assert_called_once_with("12345", save_dir, True)
                    assert result == [save_dir / "roster.csv"]

    def test_send_roster_integration(self):
        """Integration test: upload a roster CSV to a real course.

        Requires RUN_GRADESCOPE_UPLOAD_TEST=1 and a valid auth state.
        """
        if os.getenv("RUN_GRADESCOPE_UPLOAD_TEST") != "1":
            pytest.skip("Set RUN_GRADESCOPE_UPLOAD_TEST=1 to run this integration test.")

        client = GradescopeClient()
        roster_path = Path(__file__).parent / "fixtures" / "local" / "1227659_roster_with_sections.csv"
        if not roster_path.exists():
            pytest.skip("Fixture roster file not found.")

        if not client.auth_state_path.exists():
            pytest.skip("Auth state not found. Run gradescope client authenticate first.")

        client.send_roster(course="1227659", csv_path=roster_path, headless=True)

    def test_fetch_courses(self):
        """Test fetch_courses method with mock."""
        from edubag.gradescope.course import Course

        client = GradescopeClient()

        # Mock authentication state path to exist
        with patch("pathlib.Path.exists", return_value=True):
            # Mock the session method
            mock_courses = [
                Course(course_id="123456", course_name="Test Course 1"),
                Course(course_id="789012", course_name="Test Course 2"),
            ]
            with patch.object(
                client,
                "_fetch_courses_session",
                return_value=mock_courses,
            ) as mock_session:
                result = client.fetch_courses(headless=True)

                # Verify the session method was called
                mock_session.assert_called_once_with(None, True)
                assert len(result) == 2

    def test_fetch_courses_with_term_filter(self):
        """Test fetch_courses method with term filter."""
        from edubag.albert.term import Season, Term
        from edubag.gradescope.course import Course

        client = GradescopeClient()
        term = Term(2025, Season.FALL)

        # Mock authentication state path to exist
        with patch("pathlib.Path.exists", return_value=True):
            # Mock the session method
            mock_courses = [Course(course_id="123456", course_name="Test Course")]
            with patch.object(
                client,
                "_fetch_courses_session",
                return_value=mock_courses,
            ) as mock_session:
                result = client.fetch_courses(term=term, headless=True)

                # Verify the session method was called with the term
                mock_session.assert_called_once_with(term, True)
                assert len(result) == 1

    def test_fetch_assignments_stub(self):
        """Test that fetch_assignments is a stub."""
        client = GradescopeClient()

        # Mock authentication state path to exist
        with patch("pathlib.Path.exists", return_value=True):
            assignments = client.fetch_assignments(course_id="123456", headless=True)
            assert assignments == []
