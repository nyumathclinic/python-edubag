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
from edubag.gradescope import client as gs_client
from edubag.gradescope.client import GradescopeClient


class TestGradescopeClient:
    """Test the GradescopeClient class."""

    @pytest.mark.parametrize(
        ("is_published", "expected"),
        [(True, True), (False, False)],
    )
    def test_assignment_publication_uses_table_metadata(self, is_published, expected):
        page = Mock()

        result = GradescopeClient._is_assignment_published(
            {"is_published": is_published}, page
        )

        assert result is expected
        page.get_by_role.assert_not_called()

    @pytest.mark.parametrize(
        "button_name",
        ["Unpublish Grades", "Unpublish All Grades"],
    )
    def test_assignment_publication_falls_back_to_unpublish_button(self, button_name):
        page = Mock()
        page.get_by_role.side_effect = lambda *args, **kwargs: Mock(
            count=Mock(return_value=kwargs["name"] == button_name)
        )

        assert GradescopeClient._is_assignment_published({}, page)

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


class TestReplaceTemplatePdf:
    """Test replacing an assignment's template PDF (no network)."""

    @staticmethod
    def _client(tmp_path: Path) -> GradescopeClient:
        auth = tmp_path / "auth.json"
        auth.write_text("{}")
        return GradescopeClient(auth_state_path=auth)

    @staticmethod
    def _pdf(tmp_path: Path, name: str = "ps.pdf", content: bytes = b"%PDF-1.7 synthetic") -> Path:
        path = tmp_path / name
        path.write_bytes(content)
        return path

    @pytest.mark.parametrize(
        ("submission_type", "expected"),
        [("image", False), ("pdf", True), (None, True), ("something-new", True)],
    )
    def test_template_has_regions(self, submission_type, expected):
        assert gs_client.template_has_regions(submission_type) is expected

    def test_assignment_url(self, tmp_path):
        client = self._client(tmp_path)
        assert client._assignment_url("123", "456") == f"{client.base_url}/courses/123/assignments/456"
        assert (
            client._assignment_url("https://www.gradescope.com/courses/123/", "456")
            == "https://www.gradescope.com/courses/123/assignments/456"
        )

    def test_find_assignment_id(self):
        table = [
            {"type": "assignment", "title": "Problem Set 7", "url": "/courses/1/assignments/70"},
            {"type": "assignment", "title": "Quiz 1", "url": "/courses/1/assignments/10"},
            {"type": "assignment", "title": "Quiz  1 ", "url": "/courses/1/assignments/11"},
            {"type": "assignment_container", "title": "Midterm", "url": "/courses/1/assignment_containers/5"},
        ]
        assert gs_client.find_assignment_id(table, "problem set 7") == "70"
        assert gs_client.find_assignment_id(table, "  Problem   Set 7 ") == "70"
        with pytest.raises(LookupError, match="No assignment titled 'Problem Set 8'.*'Problem Set 7'"):
            gs_client.find_assignment_id(table, "Problem Set 8")
        with pytest.raises(LookupError, match="More than one.*10, 11"):
            gs_client.find_assignment_id(table, "Quiz 1")
        with pytest.raises(LookupError, match="No assignment titled 'Midterm'"):
            gs_client.find_assignment_id(table, "Midterm")

    def test_validate_pdf(self, tmp_path):
        assert gs_client.validate_pdf(self._pdf(tmp_path)) == tmp_path / "ps.pdf"
        with pytest.raises(FileNotFoundError):
            gs_client.validate_pdf(tmp_path / "missing.pdf")
        with pytest.raises(ValueError, match="Not a .pdf file"):
            gs_client.validate_pdf(self._pdf(tmp_path, "ps.txt"))
        with pytest.raises(ValueError, match="does not look like a PDF"):
            gs_client.validate_pdf(self._pdf(tmp_path, "fake.pdf", b"hello"))

    def test_bad_pdf_rejected_before_browser(self, tmp_path, monkeypatch):
        client = self._client(tmp_path)
        monkeypatch.setattr(client, "_replace_template_pdf_session", lambda *a: pytest.fail("session started"))
        with pytest.raises(ValueError):
            client.replace_template_pdf("123", "456", self._pdf(tmp_path, "fake.pdf", b"hello"))

    def test_retry_after_reauth_keeps_upload_state(self, tmp_path, monkeypatch):
        """A session that expires after the upload is retried without uploading again."""
        client = self._client(tmp_path)
        monkeypatch.setattr(client, "authenticate", lambda headless=False: None)
        seen = []

        def session(course, assignment, pdf_path, allow, dry_run, headless, state):
            seen.append(dict(state))
            if not state.get("uploaded"):
                state["uploaded"] = True
                raise RuntimeError("Authentication session expired. Please re-authenticate.")
            return {"uploaded": True, "outline_saved": True}

        monkeypatch.setattr(client, "_replace_template_pdf_session", session)
        result = client.replace_template_pdf("123", "456", self._pdf(tmp_path))
        assert result == {"uploaded": True, "outline_saved": True}
        assert seen == [{}, {"uploaded": True}]

    def test_other_errors_are_not_retried(self, tmp_path, monkeypatch):
        client = self._client(tmp_path)
        monkeypatch.setattr(client, "authenticate", lambda headless=False: pytest.fail("re-authenticated"))
        calls = []

        def session(*args):
            calls.append(1)
            raise RuntimeError("Save Outline failed: HTTP 500")

        monkeypatch.setattr(client, "_replace_template_pdf_session", session)
        with pytest.raises(RuntimeError, match="Save Outline failed"):
            client.replace_template_pdf("123", "456", self._pdf(tmp_path))
        assert len(calls) == 1

    def test_cli_help(self):
        from typer.testing import CliRunner

        from edubag.gradescope import app

        result = CliRunner().invoke(app, ["client", "replace-pdf", "--help"])
        assert result.exit_code == 0
        assert "--exam-ok" in result.output

