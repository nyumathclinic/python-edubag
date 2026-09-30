"""CLI commands to automate the Poll Everywhere web client."""

import json
from pathlib import Path
from typing import Annotated

import typer

from edubag import app as main_app

from .client import Assignment, Class, Client

app = typer.Typer(help="Automate Poll Everywhere web client interactions")


def _class_to_dict(cls: Class) -> dict:
    return {
        "id": cls.id,
        "name": cls.name,
        "start": cls.start.isoformat() if cls.start else None,
        "end": cls.end.isoformat() if cls.end else None,
        "last_roster_sync": cls.last_roster_sync.isoformat() if cls.last_roster_sync else None,
    }


def _assignment_to_dict(assignment: Assignment) -> dict:
    return {
        "id": assignment.id,
        "name": assignment.name,
        "last_grade_sync": assignment.last_grade_sync.isoformat() if assignment.last_grade_sync else None,
        "class_id": assignment.class_id,
    }


@app.command()
def authenticate(
    base_url: Annotated[
        str | None, typer.Option(help="Override Poll Everywhere base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to save auth state JSON")
    ] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = False,
) -> None:
    """Open Poll Everywhere for NYU SSO login and persist authentication state."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        client.authenticate(headless=headless)
        typer.echo("Authentication state saved.")
    except Exception as e:
        typer.echo(f"Authentication failed: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command("fetch-class")
def fetch_class(
    id: Annotated[int, typer.Argument(help="Poll Everywhere class (course connection) id")],
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Poll Everywhere base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Fetch details about a connected class."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        cls = client.fetch_class(id, headless=headless)
        typer.echo(json.dumps(_class_to_dict(cls), indent=2))
    except Exception as e:
        typer.echo(f"Error fetching class: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command("fetch-assignment")
def fetch_assignment(
    id: Annotated[int, typer.Argument(help="Poll Everywhere assignment id")],
    class_id: Annotated[
        int | None,
        typer.Option(help="Poll Everywhere class id that owns the assignment"),
    ] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Poll Everywhere base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Fetch details about an assignment."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        assignment = client.fetch_assignment(id, class_id=class_id, headless=headless)
        typer.echo(json.dumps(_assignment_to_dict(assignment), indent=2))
    except Exception as e:
        typer.echo(f"Error fetching assignment: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command("sync-roster")
def sync_roster(
    class_id: Annotated[int, typer.Argument(help="Poll Everywhere class (course connection) id")],
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Poll Everywhere base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Sync a class's roster to the LMS."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        cls = client.fetch_class(class_id, headless=headless)
        client.sync_roster_to_lms(cls, headless=headless)
        typer.echo(f"Synced roster for class {cls.id} ({cls.name}).")
    except Exception as e:
        typer.echo(f"Error syncing roster: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command("sync-assignment")
def sync_assignment(
    assignment_id: Annotated[int, typer.Argument(help="Poll Everywhere assignment id")],
    class_id: Annotated[int, typer.Argument(help="Poll Everywhere class id that owns the assignment")],
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Poll Everywhere base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Sync a single assignment's grades to the LMS."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        assignment = client.fetch_assignment(assignment_id, class_id=class_id, headless=headless)
        client.sync_assignment_to_lms(assignment, class_id=class_id, headless=headless)
        typer.echo(f"Synced grades for assignment {assignment.id} ({assignment.name}).")
    except Exception as e:
        typer.echo(f"Error syncing assignment: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command("sync-all-assignments")
def sync_all_assignments(
    class_id: Annotated[int, typer.Argument(help="Poll Everywhere class (course connection) id")],
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Poll Everywhere base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Sync all eligible assignments in a class to the LMS."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        cls = client.fetch_class(class_id, headless=headless)
        synced = client.sync_all_assignments_to_lms(cls, headless=headless)
        typer.echo(f"Synced {len(synced)} assignment(s) for class {cls.id} ({cls.name}).")
        for assignment in synced:
            typer.echo(f"  - {assignment.id}: {assignment.name}")
    except Exception as e:
        typer.echo(f"Error syncing assignments: {e}", err=True)
        raise typer.Exit(code=1) from e


# Register the polleverywhere app as a subcommand with the main app
main_app.add_typer(app, name="polleverywhere")
