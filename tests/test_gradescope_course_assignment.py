#!/usr/bin/env python
"""Tests for Course and Assignment classes."""

from datetime import datetime
from pathlib import Path

from edubag.gradescope.assignment import Assignment
from edubag.gradescope.course import Course


class TestCourse:
    """Test the Course class."""

    def test_course_initialization(self):
        """Test basic course initialization."""
        course = Course(
            course_id="123456",
            course_number="MATH-UA 122.006",
            course_name="MATH-UA 122.006 Calculus II, Spring 2026",
        )
        assert course.course_id == "123456"
        assert course.course_number == "MATH-UA 122.006"
        assert course.course_name == "MATH-UA 122.006 Calculus II, Spring 2026"

    def test_course_url_property(self):
        """Test course URL generation."""
        course = Course(course_id="123456")
        assert course.url == "https://gradescope.com/courses/123456"

    def test_course_str_representation(self):
        """Test string representation of course."""
        course = Course(course_id="123456", course_name="Test Course")
        str_repr = str(course)
        assert "123456" in str_repr
        assert "Test Course" in str_repr

    def test_course_from_dict(self):
        """Test creating a course from a dictionary."""
        data = {
            "course_id": "123456",
            "course_number": "MATH-UA 122",
            "course_name": "Calculus II",
            "instructors": ["John Doe", "Jane Smith"],
            "lms_course_id": "78910",
            "lms_course_name": "Calculus II - Spring 2026",
        }
        course = Course.from_dict(data)
        assert course.course_id == "123456"
        assert course.course_number == "MATH-UA 122"
        assert course.course_name == "Calculus II"
        assert course.instructors == ["John Doe", "Jane Smith"]
        assert course.lms_course_id == "78910"
        assert course.lms_course_name == "Calculus II - Spring 2026"

    def test_course_to_dict(self):
        """Test converting a course to a dictionary."""
        course = Course(
            course_id="123456",
            course_number="MATH-UA 122",
            course_name="Calculus II",
            instructors=["John Doe"],
            lms_course_id="78910",
        )
        data = course.to_dict()
        assert data["course_id"] == "123456"
        assert data["course_number"] == "MATH-UA 122"
        assert data["course_name"] == "Calculus II"
        assert data["instructors"] == ["John Doe"]
        assert data["lms_course_id"] == "78910"

    def test_course_get_assignments_stub(self):
        """Test that get_assignments is a stub."""
        course = Course(course_id="123456")
        assignments = course.get_assignments()
        assert assignments == []


class TestAssignment:
    """Test the Assignment class."""

    def test_assignment_initialization(self):
        """Test basic assignment initialization."""
        assignment = Assignment(
            assignment_id="789",
            name="Quiz 1",
            course_id="123456",
        )
        assert assignment.assignment_id == "789"
        assert assignment.name == "Quiz 1"
        assert assignment.course_id == "123456"

    def test_assignment_url_property(self):
        """Test assignment URL generation."""
        assignment = Assignment(
            assignment_id="789",
            name="Quiz 1",
            course_id="123456",
        )
        assert assignment.url == "https://gradescope.com/courses/123456/assignments/789"

    def test_assignment_str_representation(self):
        """Test string representation of assignment."""
        assignment = Assignment(
            assignment_id="789",
            name="Quiz 1",
            course_id="123456",
        )
        str_repr = str(assignment)
        assert "789" in str_repr
        assert "Quiz 1" in str_repr

    def test_assignment_from_dict(self):
        """Test creating an assignment from a dictionary."""
        data = {
            "assignment_id": "789",
            "name": "Quiz 1",
            "course_id": "123456",
            "template_pdf": "/path/to/template.pdf",
            "release_date": "2026-02-01T10:00:00",
            "due_date": "2026-02-08T23:59:59",
            "total_points": 100.0,
        }
        assignment = Assignment.from_dict(data)
        assert assignment.assignment_id == "789"
        assert assignment.name == "Quiz 1"
        assert assignment.course_id == "123456"
        assert assignment.template_pdf == Path("/path/to/template.pdf")
        assert isinstance(assignment.release_date, datetime)
        assert isinstance(assignment.due_date, datetime)
        assert assignment.total_points == 100.0

    def test_assignment_to_dict(self):
        """Test converting an assignment to a dictionary."""
        assignment = Assignment(
            assignment_id="789",
            name="Quiz 1",
            course_id="123456",
            template_pdf=Path("/path/to/template.pdf"),
            release_date=datetime(2026, 2, 1, 10, 0, 0),
            due_date=datetime(2026, 2, 8, 23, 59, 59),
            total_points=100.0,
        )
        data = assignment.to_dict()
        assert data["assignment_id"] == "789"
        assert data["name"] == "Quiz 1"
        assert data["course_id"] == "123456"
        assert data["template_pdf"] == "/path/to/template.pdf"
        assert data["release_date"] == "2026-02-01T10:00:00"
        assert data["due_date"] == "2026-02-08T23:59:59"
        assert data["total_points"] == 100.0

    def test_assignment_create_stub(self):
        """Test that create is a stub."""
        assignment = Assignment.create(
            course_id="123456",
            name="New Quiz",
            total_points=50.0,
        )
        assert assignment.assignment_id == "stub_id"
        assert assignment.name == "New Quiz"
        assert assignment.course_id == "123456"
        assert assignment.total_points == 50.0

    def test_assignment_update_stub(self):
        """Test that update is a stub."""
        assignment = Assignment(
            assignment_id="789",
            name="Quiz 1",
            course_id="123456",
        )
        # This should not raise an error, just log warnings
        assignment.update(name="Quiz 1 - Updated")

    def test_assignment_delete_stub(self):
        """Test that delete is a stub."""
        assignment = Assignment(
            assignment_id="789",
            name="Quiz 1",
            course_id="123456",
        )
        # This should not raise an error, just log warnings
        assignment.delete()

    def test_assignment_from_dict_with_none_values(self):
        """Test creating an assignment from a dictionary with None values."""
        data = {
            "assignment_id": "789",
            "name": "Quiz 1",
            "course_id": "123456",
            "template_pdf": None,
            "release_date": None,
            "due_date": None,
            "total_points": None,
        }
        assignment = Assignment.from_dict(data)
        assert assignment.assignment_id == "789"
        assert assignment.name == "Quiz 1"
        assert assignment.course_id == "123456"
        assert assignment.template_pdf is None
        assert assignment.release_date is None
        assert assignment.due_date is None
        assert assignment.total_points is None
