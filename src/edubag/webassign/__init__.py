"""CLI commands for WebAssign."""

from pathlib import Path
from typing import Annotated

import typer

from edubag import app as main_app

from .client import WebAssignClient

app = typer.Typer(help="WebAssign management commands")

# Nested Typer app for web client automation
client_app = typer.Typer(help="Automate WebAssign web client interactions")


@client_app.command()
def authenticate(
    base_url: Annotated[
        str | None, typer.Option(help="Override WebAssign base URL")
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
    """Open WebAssign for Cengage login and persist authentication state."""
    client = WebAssignClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        client.authenticate(headless=headless)
        typer.echo("Authentication state saved.")
    except Exception as e:
        typer.echo(f"Authentication failed: {e}", err=True)
        raise typer.Exit(code=1) from e


BaseUrl = Annotated[str | None, typer.Option(help="Override WebAssign base URL")]
AuthStatePath = Annotated[Path | None, typer.Option(help="Path to stored auth state JSON")]
Headless = Annotated[
    bool,
    typer.Option(
        "--headless/--headed",
        help="Run browser headless (for automation) or headed (for debugging)",
    ),
]
Sections = Annotated[
    list[str],
    typer.Argument(help="WebAssign section ID(s), all from one course (see list-sections)"),
]
SaveDir = Annotated[Path | None, typer.Option(help="Directory to save the file (default: current directory)")]
FileType = Annotated[str, typer.Option(help="File type: csv, tsv or excel")]


@client_app.command("list-sections")
def list_sections(
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
    headless: Headless = True,
) -> None:
    """List current WebAssign sections and their IDs."""
    client = WebAssignClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        for s in client.list_sections(headless=headless):
            typer.echo(f"{s['section_id']}\t{s['course']}, section {s['section']}")
    except Exception as e:
        typer.echo(f"Error listing sections: {e}", err=True)
        raise typer.Exit(code=1) from e


@client_app.command("save-roster")
def save_roster(
    sections: Sections,
    save_dir: SaveDir = None,
    students: Annotated[str, typer.Option(help="Students to include: current, dropped or all")] = "current",
    include_faculty: Annotated[
        bool, typer.Option("--include-faculty", help="Include faculty with student access")
    ] = False,
    file_type: FileType = "csv",
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
    headless: Headless = True,
) -> None:
    """Download the roster for one or more sections of a course."""
    client = WebAssignClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        path = client.save_roster(
            sections,
            save_dir=save_dir,
            students=students,
            include_faculty=include_faculty,
            file_type=file_type,
            headless=headless,
        )
        typer.echo(f"Roster saved to {path}")
    except Exception as e:
        typer.echo(f"Error saving roster: {e}", err=True)
        raise typer.Exit(code=1) from e


@client_app.command("save-scores")
def save_scores(
    sections: Sections,
    save_dir: SaveDir = None,
    assignments: Annotated[
        str, typer.Option(help="Assignments to include: all, past-current, past, current or future")
    ] = "all",
    students: Annotated[str, typer.Option(help="Students to include: current, dropped or all")] = "current",
    include_faculty: Annotated[
        bool, typer.Option("--include-faculty", help="Include faculty with student access")
    ] = False,
    file_type: FileType = "csv",
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
    headless: Headless = True,
) -> None:
    """Download assignment scores for one or more sections of a course."""
    client = WebAssignClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        path = client.save_scores(
            sections,
            save_dir=save_dir,
            assignments=assignments,
            students=students,
            include_faculty=include_faculty,
            file_type=file_type,
            headless=headless,
        )
        typer.echo(f"Scores saved to {path}")
    except Exception as e:
        typer.echo(f"Error saving scores: {e}", err=True)
        raise typer.Exit(code=1) from e


@client_app.command("save-gradebook")
def save_gradebook(
    sections: Sections,
    save_dir: SaveDir = None,
    file_type: FileType = "csv",
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
    headless: Headless = True,
) -> None:
    """Download the GradeBook for one or more sections of a course.

    Requires the class's GradeBook to be set up in WebAssign; otherwise use save-scores.
    """
    client = WebAssignClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        path = client.save_gradebook(sections, save_dir=save_dir, file_type=file_type, headless=headless)
        typer.echo(f"GradeBook saved to {path}")
    except Exception as e:
        typer.echo(f"Error saving gradebook: {e}", err=True)
        raise typer.Exit(code=1) from e


@client_app.command("sync-scores")
def sync_scores(
    section: Annotated[str, typer.Argument(help="WebAssign section ID (see list-sections)")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Find 'Send Scores To LMS' but do not click it")
    ] = False,
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
    headless: Headless = True,
) -> None:
    """Send a section's scores to the linked LMS (allowed once per hour).

    A sync can overwrite score edits made in the LMS gradebook.
    """
    client = WebAssignClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        result = client.sync_scores(section, dry_run=dry_run, headless=headless)
    except Exception as e:
        typer.echo(f"Error syncing scores: {e}", err=True)
        raise typer.Exit(code=1) from e
    if result["dry_run"]:
        typer.echo(f"Dry run: {result['message']} for section {result['section']}; nothing sent.")
    elif result["sent"]:
        typer.echo(f"{result['message']} (section {result['section']}).")
    else:
        typer.echo(f"{result['message']} (section {result['section']}, HTTP {result['status']}).", err=True)
        raise typer.Exit(code=1)


@client_app.command("list-assignments")
def list_assignments(
    section: Annotated[str, typer.Argument(help="WebAssign section ID (see list-sections)")],
    when: Annotated[str, typer.Option(help="Assignments to list: all, past, current or future")] = "all",
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
    headless: Headless = True,
) -> None:
    """List a section's assignments and their IDs."""
    client = WebAssignClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        for a in client.list_assignments(section, when=when, headless=headless):
            typer.echo(f"{a['aid']}\t{a['due']}\t{a['name']}")
    except Exception as e:
        typer.echo(f"Error listing assignments: {e}", err=True)
        raise typer.Exit(code=1) from e


@client_app.command("export-assignment")
def export_assignment(
    assignments: Annotated[
        list[str], typer.Argument(help="WebAssign assignment ID(s) (see list-assignments)")
    ],
    save_dir: Annotated[
        Path | None, typer.Option(help="Parent directory for the exports (default: current directory)")
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Re-export questions that were already exported")
    ] = False,
    keep_html: Annotated[
        bool, typer.Option("--keep-html", help="Also save the editor pages' HTML")
    ] = False,
    delay: Annotated[float, typer.Option(help="Seconds to wait between questions")] = 1.0,
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
    headless: Headless = True,
) -> None:
    """Export assignments and the source of their questions as JSON.

    Reads the Assignment and Question Editors without saving anything.
    """
    client = WebAssignClient(base_url=base_url, auth_state_path=auth_state_path)
    for assignment in assignments:
        try:
            path = client.export_assignment(
                assignment,
                save_dir=save_dir,
                overwrite=overwrite,
                keep_html=keep_html,
                delay=delay,
                headless=headless,
            )
        except Exception as e:
            typer.echo(f"Error exporting assignment {assignment}: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"Assignment {assignment} exported to {path}")


def _convert(kind: str, path: Path, section: str | None, output_dir: Path | None) -> None:
    from .export import WebAssignRoster, WebAssignScores

    parser = WebAssignRoster if kind == "roster" else WebAssignScores
    data = parser.from_csv(path)
    stem = f"{kind}_{section}" if section else path.stem
    out = output_dir or path.parent
    for target in (data.to_csv(out / f"{stem}.csv"), data.to_json(out / f"{stem}.json")):
        typer.echo(str(target))


ExportPath = Annotated[Path, typer.Argument(help="CSV file downloaded from WebAssign")]
SectionId = Annotated[
    str | None, typer.Option(help="WebAssign section ID for the output file names (default: input name)")
]
OutputDir = Annotated[Path | None, typer.Option(help="Output directory (default: next to the input)")]


@app.command("convert-roster")
def convert_roster(path: ExportPath, section: SectionId = None, output_dir: OutputDir = None) -> None:
    """Write a WebAssign roster download as a plain CSV and a JSON file."""
    _convert("roster", path, section, output_dir)


@app.command("convert-scores")
def convert_scores(path: ExportPath, section: SectionId = None, output_dir: OutputDir = None) -> None:
    """Write a WebAssign scores download as a plain CSV and a JSON file."""
    _convert("scores", path, section, output_dir)


# Register the webassign app as a subcommand with the main app
main_app.add_typer(app, name="webassign")
app.add_typer(client_app, name="client")
