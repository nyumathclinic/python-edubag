"""Module for representing and interacting with Gradescope courses."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from edubag.gradescope.assignment import Assignment


@dataclass
class Course:
    """Represents a Gradescope course.

    Attributes:
        course_id: The Gradescope course ID
        course_number: The course number (e.g., "MATH-UA 122.006")
        course_name: The full course name (e.g., "MATH-UA 122.006 Calculus II, Spring 2026")
        instructors: List of instructor names
        lms_course_id: LMS course ID if linked
        lms_course_name: LMS course name if linked
    """

    course_id: str
    course_number: str | None = None
    course_name: str | None = None
    instructors: list[str] | None = None
    lms_course_id: str | None = None
    lms_course_name: str | None = None

    def __str__(self) -> str:
        """String representation of the course."""
        return f"Course(id={self.course_id}, name={self.course_name or self.course_number or 'Unknown'})"

    def __repr__(self) -> str:
        """Detailed representation of the course."""
        return (
            f"Course(course_id={self.course_id!r}, "
            f"course_number={self.course_number!r}, "
            f"course_name={self.course_name!r}, "
            f"instructors={self.instructors!r}, "
            f"lms_course_id={self.lms_course_id!r}, "
            f"lms_course_name={self.lms_course_name!r})"
        )

    @property
    def url(self) -> str:
        """Get the URL for this course on Gradescope."""
        return f"https://gradescope.com/courses/{self.course_id}"

    def get_assignments(self) -> list["Assignment"]:
        """Get list of assignments for this course.

        Returns:
            List of Assignment objects

        Note:
            This is a stub method that will be implemented with Playwright automation.
        """
        logger.warning("Course.get_assignments() is not yet implemented (stub)")
        return []

    @classmethod
    def from_dict(cls, data: dict) -> "Course":
        """Create a Course object from a dictionary.

        Args:
            data: Dictionary containing course data

        Returns:
            Course object
        """
        return cls(
            course_id=data.get("course_id", ""),
            course_number=data.get("course_number"),
            course_name=data.get("course_name"),
            instructors=data.get("instructors"),
            lms_course_id=data.get("lms_course_id"),
            lms_course_name=data.get("lms_course_name"),
        )

    def to_dict(self) -> dict:
        """Convert the Course object to a dictionary.

        Returns:
            Dictionary representation of the course
        """
        return {
            "course_id": self.course_id,
            "course_number": self.course_number,
            "course_name": self.course_name,
            "instructors": self.instructors,
            "lms_course_id": self.lms_course_id,
            "lms_course_name": self.lms_course_name,
        }
