"""Module for representing and interacting with Gradescope assignments."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger


@dataclass
class Assignment:
    """Represents a Gradescope assignment.

    Attributes:
        assignment_id: The Gradescope assignment ID
        name: The assignment name/title
        course_id: The ID of the course this assignment belongs to
        template_pdf: Path to the template PDF file (if applicable)
        release_date: When the assignment is released to students
        due_date: When the assignment is due
        total_points: Total points possible for the assignment
    """

    assignment_id: str
    name: str
    course_id: str
    template_pdf: Path | None = None
    release_date: datetime | None = None
    due_date: datetime | None = None
    total_points: float | None = None

    def __str__(self) -> str:
        """String representation of the assignment."""
        return f"Assignment(id={self.assignment_id}, name={self.name})"

    def __repr__(self) -> str:
        """Detailed representation of the assignment."""
        return (
            f"Assignment(assignment_id={self.assignment_id!r}, "
            f"name={self.name!r}, "
            f"course_id={self.course_id!r}, "
            f"template_pdf={self.template_pdf!r}, "
            f"release_date={self.release_date!r}, "
            f"due_date={self.due_date!r}, "
            f"total_points={self.total_points!r})"
        )

    @property
    def url(self) -> str:
        """Get the URL for this assignment on Gradescope."""
        return f"https://gradescope.com/courses/{self.course_id}/assignments/{self.assignment_id}"

    def update(self, name: str | None = None, template_pdf: Path | None = None) -> None:
        """Update assignment details.

        Args:
            name: New name for the assignment
            template_pdf: New template PDF file path

        Note:
            This is a stub method that will be implemented with Playwright automation.
        """
        logger.warning("Assignment.update() is not yet implemented (stub)")
        if name is not None:
            logger.info(f"Would update assignment name to: {name}")
        if template_pdf is not None:
            logger.info(f"Would update template PDF to: {template_pdf}")

    def delete(self) -> None:
        """Delete this assignment.

        Note:
            This is a stub method that will be implemented with Playwright automation.
        """
        logger.warning("Assignment.delete() is not yet implemented (stub)")
        logger.info(f"Would delete assignment: {self.name} (id={self.assignment_id})")

    @classmethod
    def create(
        cls,
        course_id: str,
        name: str,
        template_pdf: Path | None = None,
        release_date: datetime | None = None,
        due_date: datetime | None = None,
        total_points: float | None = None,
    ) -> "Assignment":
        """Create a new assignment on Gradescope.

        Args:
            course_id: The ID of the course to create the assignment in
            name: The assignment name/title
            template_pdf: Path to the template PDF file (if applicable)
            release_date: When the assignment should be released to students
            due_date: When the assignment is due
            total_points: Total points possible for the assignment

        Returns:
            The created Assignment object

        Note:
            This is a stub method that will be implemented with Playwright automation.
        """
        logger.warning("Assignment.create() is not yet implemented (stub)")
        logger.info(f"Would create assignment: {name} in course {course_id}")
        # Return a stub assignment with a placeholder ID
        return cls(
            assignment_id="stub_id",
            name=name,
            course_id=course_id,
            template_pdf=template_pdf,
            release_date=release_date,
            due_date=due_date,
            total_points=total_points,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "Assignment":
        """Create an Assignment object from a dictionary.

        Args:
            data: Dictionary containing assignment data

        Returns:
            Assignment object
        """
        # Parse dates if they're strings
        release_date = data.get("release_date")
        if isinstance(release_date, str):
            release_date = datetime.fromisoformat(release_date)

        due_date = data.get("due_date")
        if isinstance(due_date, str):
            due_date = datetime.fromisoformat(due_date)

        # Parse Path if it's a string
        template_pdf = data.get("template_pdf")
        if isinstance(template_pdf, str):
            template_pdf = Path(template_pdf)

        return cls(
            assignment_id=data.get("assignment_id", ""),
            name=data.get("name", ""),
            course_id=data.get("course_id", ""),
            template_pdf=template_pdf,
            release_date=release_date,
            due_date=due_date,
            total_points=data.get("total_points"),
        )

    def to_dict(self) -> dict:
        """Convert the Assignment object to a dictionary.

        Returns:
            Dictionary representation of the assignment
        """
        return {
            "assignment_id": self.assignment_id,
            "name": self.name,
            "course_id": self.course_id,
            "template_pdf": str(self.template_pdf) if self.template_pdf else None,
            "release_date": self.release_date.isoformat() if self.release_date else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "total_points": self.total_points,
        }
