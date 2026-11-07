"""AIM (accessiblelearning.com) instructor portal: testing-center exams for NYU's Moses Center."""

import json
from pathlib import Path
from typing import Annotated

import typer

from edubag import app as main_app

from .client import AimClient, AimError
from .portal import ExamFilter

app = typer.Typer(help="AIM instructor portal (Moses Center testing accommodations)")
client_app = typer.Typer(help="Automate the AIM instructor portal")

Headless = Annotated[
    bool,
    typer.Option("--headless/--headed", help="Run browser headless (for automation) or headed (for debugging)"),
]
AuthStatePath = Annotated[Path | None, typer.Option(help="Path to the stored auth state JSON")]
BaseUrl = Annotated[str | None, typer.Option(help="Override the AIM base URL (…/NYU/)")]
Crn = Annotated[list[str] | None, typer.Option("--crn", help="CRN (Albert class number); repeatable")]
ExamType = Annotated[list[str] | None, typer.Option("--type", help="Exam type, e.g. Exam or Quiz; repeatable")]
Date = Annotated[str | None, typer.Option("--date", help="Exam date (YYYY-MM-DD); alone, only this date")]
Until = Annotated[str | None, typer.Option("--until", help="Last exam date (YYYY-MM-DD), inclusive")]
GraceDays = Annotated[int | None, typer.Option("--grace-days", help="Instead of --until: DATE plus this many days")]
Term = Annotated[str | None, typer.Option(help='Fail unless AIM shows this term, e.g. "Fall 2026"')]


def _filter(crn, type_, date, until, grace_days) -> ExamFilter:
    try:
        return ExamFilter.build(crn=crn, type=type_, date=date, until=until, grace_days=grace_days)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


@client_app.command()
def authenticate(
    headless: Headless = False,
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
) -> None:
    """Sign in to AIM through NYU SSO and save the authentication state."""
    client = AimClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        client.authenticate(headless=headless)
    except Exception as e:
        typer.echo(f"Authentication failed: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo("Authentication state saved.")


@client_app.command("list-exams")
def list_exams(
    crn: Crn = None,
    exam_type: ExamType = None,
    date: Date = None,
    until: Until = None,
    grace_days: GraceDays = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of a table")] = False,
    term: Term = None,
    headless: Headless = True,
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
) -> None:
    """List completed testing-center exams, optionally filtered."""
    flt = _filter(crn, exam_type, date, until, grace_days)
    client = AimClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        rows = client.list_exams(flt, headless=headless, term=term)
    except AimError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    if as_json:
        typer.echo(json.dumps([r.to_json() for r in rows], indent=2))
        return
    for r in rows:
        typer.echo(
            f"{r.crn}  {r.subject} {r.course}.{r.section}  {r.type:<6} {r.exam_date}  "
            f"{r.time_range:<28} uploaded {r.uploaded_on}  {r.name}"
        )
    typer.echo(f"{len(rows)} exam(s) matching {flt.describe()}", err=True)


@client_app.command("save-exams")
def save_exams(
    save_dir: Annotated[Path, typer.Option(help="Directory to extract the exam PDFs into")],
    crn: Crn = None,
    exam_type: ExamType = None,
    date: Date = None,
    until: Until = None,
    grace_days: GraceDays = None,
    zip_dir: Annotated[Path | None, typer.Option(help="Where to keep the ZIPs (default SAVE_DIR/_zips)")] = None,
    term: Term = None,
    headless: Headless = True,
    base_url: BaseUrl = None,
    auth_state_path: AuthStatePath = None,
) -> None:
    """Download completed exams (10 per ZIP) and extract their PDFs into SAVE_DIR."""
    flt = _filter(crn, exam_type, date, until, grace_days)
    if flt == ExamFilter():
        raise typer.BadParameter("Give at least one filter (e.g. --crn, --type, --date)")
    client = AimClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        batches = client.save_exams(save_dir, flt, zip_dir=zip_dir, headless=headless, term=term)
    except AimError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    for b in batches:
        for f in b.files:
            typer.echo(str(f))
    typer.echo(f"{sum(len(b.rows) for b in batches)} exam(s) in {len(batches)} ZIP(s)", err=True)


main_app.add_typer(app, name="aim")
app.add_typer(client_app, name="client")
