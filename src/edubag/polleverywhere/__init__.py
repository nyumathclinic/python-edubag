"""CLI commands to automate the Poll Everywhere web client."""

import json
from pathlib import Path
from typing import Annotated

import typer

from edubag import app as main_app

from .client import Assignment, Class, Client, Folder, Poll

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


def _folder_to_dict(folder: Folder) -> dict:
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id, "path": folder.path}


def poll_to_dict(poll: Poll) -> dict:
    """JSON-serializable form of a `Poll` (also used for ``polls.json`` listings)."""
    return {
        "id": poll.id,
        "type": poll.type,
        "title": poll.title,
        "group_id": poll.group_id,
        "permalink": poll.permalink,
        "results_count": poll.results_count,
        "created_at": poll.created_at.isoformat() if poll.created_at else None,
        "updated_at": poll.updated_at.isoformat() if poll.updated_at else None,
        "state": poll.state,
        "owner_id": poll.owner_id,
    }


RunsOption = Annotated[
    str,
    typer.Option(
        help="'all' runs (including responses archived by Clear responses) or only the 'current' run"
    ),
]


@app.command("list-folders")
def list_folders(
    base_url: Annotated[str | None, typer.Option(help="Override Poll Everywhere base URL")] = None,
    auth_state_path: Annotated[Path | None, typer.Option(help="Path to stored auth state JSON")] = None,
) -> None:
    """List all activity folders (with their ids and paths)."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        typer.echo(json.dumps([_folder_to_dict(f) for f in client.list_folders()], indent=2))
    except Exception as e:
        typer.echo(f"Error listing folders: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command("list-polls")
def list_polls(
    folder: Annotated[str, typer.Argument(help="Folder id, name, or path (from list-folders)")],
    recursive: Annotated[
        bool, typer.Option("--recursive/--no-recursive", help="Include nested folders")
    ] = True,
    base_url: Annotated[str | None, typer.Option(help="Override Poll Everywhere base URL")] = None,
    auth_state_path: Annotated[Path | None, typer.Option(help="Path to stored auth state JSON")] = None,
) -> None:
    """List the polls in a folder."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        polls = client.list_polls(folder, recursive=recursive)
        typer.echo(json.dumps([poll_to_dict(p) for p in polls], indent=2, ensure_ascii=False))
    except Exception as e:
        typer.echo(f"Error listing polls: {e}", err=True)
        raise typer.Exit(code=1) from e


def _report_failures(results: dict[int, Exception | None]) -> None:
    failed = {pid: err for pid, err in results.items() if err is not None}
    for pid, err in failed.items():
        typer.echo(f"Poll {pid}: {err}", err=True)
    typer.echo(f"Saved {len(results) - len(failed)} of {len(results)} poll(s).")
    if failed:
        raise typer.Exit(code=1)


@app.command("download-responses")
def download_responses(
    poll_ids: Annotated[list[int], typer.Argument(help="Poll Everywhere poll (activity) ids")],
    output_dir: Annotated[Path, typer.Option(help="Directory for <poll id>.csv files")] = Path("."),
    runs: RunsOption = "all",
    keep_reports: Annotated[
        bool, typer.Option(help="Keep the generated reports in Poll Everywhere")
    ] = False,
    base_url: Annotated[str | None, typer.Option(help="Override Poll Everywhere base URL")] = None,
    auth_state_path: Annotated[Path | None, typer.Option(help="Path to stored auth state JSON")] = None,
) -> None:
    """Download each poll's participant responses as <output-dir>/<poll id>.csv."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        results = client.download_responses(
            {pid: output_dir / f"{pid}.csv" for pid in poll_ids}, runs=runs, keep_reports=keep_reports
        )
    except Exception as e:
        typer.echo(f"Error downloading responses: {e}", err=True)
        raise typer.Exit(code=1) from e
    _report_failures(results)


@app.command("download-folder")
def download_folder(
    folder: Annotated[str, typer.Argument(help="Folder id, name, or path (from list-folders)")],
    output_dir: Annotated[Path, typer.Option(help="Directory for <poll id>.csv files and polls.json")],
    recursive: Annotated[
        bool, typer.Option("--recursive/--no-recursive", help="Include nested folders")
    ] = True,
    runs: RunsOption = "all",
    base_url: Annotated[str | None, typer.Option(help="Override Poll Everywhere base URL")] = None,
    auth_state_path: Annotated[Path | None, typer.Option(help="Path to stored auth state JSON")] = None,
) -> None:
    """Download participant responses for every poll (with responses) in a folder."""
    client = Client(base_url=base_url, auth_state_path=auth_state_path)
    try:
        polls = client.list_polls(folder, recursive=recursive)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "polls.json").write_text(
            json.dumps([poll_to_dict(p) for p in polls], indent=2, ensure_ascii=False)
        )
        results = client.download_responses(
            {p: output_dir / f"{p.id}.csv" for p in polls if p.results_count > 0}, runs=runs
        )
    except Exception as e:
        typer.echo(f"Error downloading responses: {e}", err=True)
        raise typer.Exit(code=1) from e
    _report_failures(results)


# Register the polleverywhere app as a subcommand with the main app
main_app.add_typer(app, name="polleverywhere")
