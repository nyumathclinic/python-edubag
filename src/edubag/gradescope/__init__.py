from pathlib import Path
from typing import Annotated, List

import typer
from loguru import logger

from edubag import app as main_app
from edubag.albert.roster import AlbertRoster
from edubag.brightspace.gradebook import Gradebook
from edubag.gradescope.roster import GradescopeRoster
from edubag.gradescope.scoresheet import (
    Scoresheet,
    SectionedScoresheet,
    VersionedScoresheet,
)

from .client import REGION_WARNING, GradescopeClient

# Create a local Typer app for gradescope subcommands
app = typer.Typer(help="Gradescope management commands")


def _load_scoresheet(input_path: Path) -> Scoresheet:
    """Load a scoresheet from a zip or CSV file."""
    if input_path.name.endswith("_Version_Set_Scores.zip"):
        return VersionedScoresheet.from_zip(input_path)
    elif input_path.name.endswith("_scores.csv"):
        return Scoresheet.from_csv(input_path)
    else:
        raise ValueError("Input file must be a Gradescope scores zip or CSV file.")


def _merge_scoresheets_to_gradebook(scoresheets: List[Scoresheet]) -> Gradebook:
    """Merge multiple scoresheets into one Brightspace gradebook.

    Creates one Brightspace grade item column per input scoresheet, joined by Username.
    """
    if not scoresheets:
        raise ValueError("Must provide at least one scoresheet to merge")

    merged = None
    used_grade_columns: set[str] = set()

    for scoresheet in scoresheets:
        gb = Gradebook.from_gradescope_scoresheet(scoresheet)
        frame = gb.grades.copy()
        grade_columns = [col for col in frame.columns if col != "Username"]

        if len(grade_columns) != 1:
            raise ValueError("Expected exactly one grade column when converting scoresheet")

        original_col = grade_columns[0]
        unique_col = original_col
        suffix = 2
        while unique_col in used_grade_columns:
            unique_col = f"{original_col} ({suffix})"
            suffix += 1

        if unique_col != original_col:
            frame = frame.rename(columns={original_col: unique_col})

        used_grade_columns.add(unique_col)
        merged = frame if merged is None else merged.merge(frame, on="Username", how="outer")

    merged_gb = Gradebook()
    merged_gb.grades = merged  # type: ignore[assignment]
    merged_gb.data = merged_gb.grades.copy()
    merged_gb.metadata = {
        "source": "gradescope_scoresheets",
        "type": "brightspace_gradebook_merged_from_gradescope",
        "items": len(used_grade_columns),
    }
    return merged_gb


@app.command("gs2bs")
def gradescope_scores_file_to_brightspace_gradebook_csv(
    inputs: Annotated[
        List[Path],
        typer.Argument(help="Path(s) to the Gradescope scores zip or CSV file(s)."),
    ],
    output: Annotated[
        Path | None,
        typer.Option(help="Path to the output Brightspace gradebook CSV file or directory."),
    ] = None,
    by_section: Annotated[
        bool, typer.Option(help="Save separate files for each section.")
    ] = False,
    merge: Annotated[
        bool,
        typer.Option(
            "--merge/--separate",
            help="Merge multiple inputs into one output or export each input separately.",
        ),
    ] = False,
):
    """Convert Gradescope scores file(s) to Brightspace gradebook CSV file(s).

    Each `input` can be either a zip file containing versioned assignment scores
    or a CSV file for unversioned assignments.

    By default, each input file generates a separate output file (--separate mode).
    Use --merge to combine multiple inputs into a single output with one grade
    column per input.

    If no `output` path is provided:
    - In separate mode: output files are created in the same directory as each input
    - In merge mode: the merged output is created in the same directory as the first input

    If `by_section` is True, separate gradebook files are created for each section.

    """
    # Load all scoresheets
    scoresheets = [_load_scoresheet(input_path) for input_path in inputs]
    
    if merge:
        # Merge mode: combine all inputs into one output
        merged_name = " + ".join([ss.name for ss in scoresheets])

        # Determine output path
        if output is None:
            output_path = inputs[0].with_name(merged_name.replace(" ", "_")).with_suffix(".csv")
        elif output.is_dir() or (not output.exists() and str(output).endswith("/")):
            # If output looks like a directory, create a filename within it
            output_path = output / Path(merged_name.replace(" ", "_")).with_suffix(".csv")
        else:
            output_path = output

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if by_section:
            # Merge each section independently so each section file includes all items.
            sectioned_inputs = [
                SectionedScoresheet(name=ss.name, scores=ss.scores).by_section()
                for ss in scoresheets
            ]
            all_sections = sorted({section for d in sectioned_inputs for section in d.keys()})

            for section in all_sections:
                section_scoresheets = [d[section] for d in sectioned_inputs if section in d]
                section_gradebook = _merge_scoresheets_to_gradebook(section_scoresheets)
                section_output = output_path.with_stem(f"{output_path.stem}_section_{section}")
                logger.info(
                    f"Writing merged Brightspace gradebook for section {section} to {section_output}"
                )
                section_gradebook.to_csv(section_output)
        else:
            merged_gradebook = _merge_scoresheets_to_gradebook(scoresheets)
            logger.info(f"Merged {len(scoresheets)} scoresheets into one Brightspace gradebook")
            merged_gradebook.to_csv(output_path)

        logger.success(f"Conversion complete. Output saved to {output_path.parent}")
    
    else:
        # Separate mode: each input generates its own output
        for i, (input_path, scoresheet) in enumerate(zip(inputs, scoresheets)):
            # Determine output path for this input
            if output is None:
                output_path = input_path.with_name(scoresheet.name.replace(" ", "_")).with_suffix(".csv")
            elif output.is_dir() or (not output.exists() and str(output).endswith("/")):
                # If output looks like a directory, create a filename within it
                output_path = output / Path(scoresheet.name.replace(" ", "_")).with_suffix(".csv")
            else:
                # If output is a file path, use it for the first input
                # For subsequent inputs, append a suffix
                if i == 0:
                    output_path = output
                else:
                    output_path = output.with_stem(f"{output.stem}_{i}")
            
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_gradebook(scoresheet, output_path, by_section)
            logger.success(f"Conversion complete for {input_path.name}. Output saved to {output_path}")


def _write_gradebook(scoresheet: Scoresheet, output_path: Path, by_section: bool) -> None:
    """Write a scoresheet as a Brightspace gradebook CSV.
    
    Args:
        scoresheet: The scoresheet to convert
        output_path: Path to the output CSV file
        by_section: Whether to split by section
    """
    if by_section:
        # Use SectionedScoresheet to split by section (handles NaN students gracefully)
        sectioned = SectionedScoresheet(name=scoresheet.name, scores=scoresheet.scores)
        sections_dict = sectioned.by_section()
        for section, section_scoresheet in sections_dict.items():
            gradebook = Gradebook.from_gradescope_scoresheet(section_scoresheet)
            section_output = output_path.with_stem(f"{output_path.stem}_section_{section}")
            logger.info(
                f"Writing Brightspace gradebook for section {section} to {section_output}"
            )
            gradebook.to_csv(section_output)
    else:
        gradebook = Gradebook.from_gradescope_scoresheet(scoresheet)
        logger.debug(f"Writing Brightspace gradebook to {output_path}")
        gradebook.to_csv(output_path)


@app.command("bs2gs")
def add_sections_to_roster_from_brightspace(
    roster_csv: Annotated[
        Path, typer.Argument(help="Path to the Gradescope roster CSV file.")
    ],
    brightspace_csv: Annotated[
        Path, typer.Argument(help="Path to the Brightspace gradebook CSV file.")
    ],
    output_csv: Annotated[
        Path | None,
        typer.Argument(
            help="Path to the output Gradescope roster CSV file with sections added."
        ),
    ] = None,
    skip_constant: Annotated[
        bool,
        typer.Option(
            "--skip-constant/--keep-constant",
            help="Skip columns with only one unique value.",
        ),
    ] = True,
) -> List[Path]:
    """Add section information to a Gradescope roster from a Brightspace gradebook CSV.

    The output CSV file can be specified with `output_csv`. If not provided,
    the output file is created in the same directory as the input roster file,
    with '_with_sections' appended to the stem of the filename.

    Args:
        roster_csv (Path): Path to the Gradescope roster CSV file.
        brightspace_csv (Path): Path to the Brightspace gradebook CSV file.
        output_csv (Path | None): Path to the output Gradescope roster CSV file.
        skip_constant (bool): Skip columns with only one unique value.

    Returns:
        List[Path]: List containing the path to the output CSV file.
    """
    # Load the Gradescope roster
    gs_roster = GradescopeRoster.from_csv(roster_csv)

    # Load the Brightspace gradebook
    bs_gradebook = Gradebook.from_csv(brightspace_csv)

    gs_roster.update_sections_from_brightspace_gradebook(
        bs_gradebook, skip_constant=skip_constant
    )

    # Save the updated roster with sections
    if output_csv is None:
        output_csv = roster_csv.with_stem(f"{roster_csv.stem}_with_sections")
    try:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        gs_roster.to_csv(output_csv)
        logger.success(f"Saved updated Gradescope roster with sections to {output_csv}")
        return [output_csv]
    except Exception as e:
        logger.error(f"Failed to save updated Gradescope roster: {e}")
        return []


# Nested Typer app for web client automation
client_app = typer.Typer(help="Automate Gradescope web client interactions")


@client_app.command()
def authenticate(
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
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
    """Open Gradescope for login and persist authentication state."""
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        client.authenticate(headless=headless)
        typer.echo("Authentication state saved.")
    except Exception as e:
        typer.echo(f"Authentication failed: {e}", err=True)
        raise typer.Exit(code=1)


@client_app.command("sync-roster")
def sync_roster(
    course: Annotated[
        str, typer.Argument(help="Gradescope course ID or URL to the course home page")
    ],
    notify: Annotated[bool, typer.Option(help="Notify added users")] = True,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Synchronize the course roster with the linked LMS."""
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        client.sync_roster(
            course=course,
            notify=notify,
            headless=headless,
        )
        typer.echo("Roster sync completed successfully.")
    except Exception as e:
        typer.echo(f"Roster sync failed: {e}", err=True)
        raise typer.Exit(code=1)


@client_app.command("fetch-details")
def fetch_class_details(
    course_name: Annotated[
        str, typer.Argument(help="Course name to match in Gradescope")
    ],
    term: Annotated[str, typer.Argument(help="Term, e.g., 'Fall 2025'")],
    output: Annotated[Path | None, typer.Option(help="Path to save output in")] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Fetch class details for a course offering and optionally save."""
    import json

    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    result = client.fetch_class_details(
        course_name=course_name,
        term=term,
        headless=headless,
        output=output,
    )

    # If output is None, pretty-print to STDOUT
    if output is None:
        typer.echo(json.dumps(result, indent=2))


@client_app.command("save-roster")
def save_roster(
    course: Annotated[
        str, typer.Argument(help="Gradescope course ID or URL to the course home page")
    ],
    save_dir: Annotated[
        Path | None, typer.Option(help="Target directory for the saved roster file")
    ] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Download the roster for a Gradescope course."""
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    result_paths = client.save_roster(
        course=course,
        save_dir=save_dir,
        headless=headless,
    )
    for p in result_paths:
        typer.echo(f"Roster saved to {p}")


@client_app.command("send-roster")
def send_roster(
    course: Annotated[
        str, typer.Argument(help="Gradescope course ID or URL to the course home page")
    ],
    csv_path: Annotated[Path, typer.Argument(help="Path to the roster CSV file to upload")],
    notify: Annotated[
        bool, typer.Option(help="Notify users by email when added to the course")
    ] = False,
    role: Annotated[
        str,
        typer.Option(help="Role to add users as. Must be one of: Student, Instructor, TA, Reader"),
    ] = "Student",
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Upload a roster CSV file to a Gradescope course.
    
    Users are added or updated based on the contents of the CSV file.
    For example, the file might include additional staff members to add to the course.
    Or it might contain section information to update existing students.    
    """
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        client.send_roster(
            course=course,
            csv_path=csv_path,
            notify=notify,
            role=role,
            headless=headless,
        )
        typer.echo("Roster upload completed successfully.")
    except Exception as e:
        typer.echo(f"Roster upload failed: {e}", err=True)
        raise typer.Exit(code=1)


@client_app.command("save-scores")
def save_scores(
    course: Annotated[
        str, typer.Argument(help="Gradescope course ID")
    ],
    assignments: Annotated[
        List[str], typer.Argument(help="One or more Gradescope assignment IDs to download")
    ],
    save_dir: Annotated[
        Path | None, typer.Option(help="Target directory for saved files")
    ] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Download scores CSV for one or more Gradescope assignments.

    Downloads `<assignment>_scores.csv` for each ASSIGNMENT ID provided
    and saves the files to SAVE_DIR (or the current directory if omitted).
    """
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    failed = False
    for assignment in assignments:
        try:
            paths = client.save_assignment_scores(
                course=course,
                assignment=assignment,
                save_dir=save_dir,
                headless=headless,
            )
            for p in paths:
                typer.echo(f"Scores saved to {p}")
        except Exception as e:
            typer.echo(f"Failed to download scores for assignment {assignment}: {e}", err=True)
            failed = True
    if failed:
        raise typer.Exit(code=1)


@client_app.command("save-container-scores")
def save_container_scores(
    course: Annotated[
        str, typer.Argument(help="Gradescope course ID")
    ],
    assignment_containers: Annotated[
        List[str],
        typer.Argument(help="One or more Gradescope assignment container IDs to download"),
    ],
    save_dir: Annotated[
        Path | None, typer.Option(help="Target directory for saved files")
    ] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Download versioned scores zip for one or more Gradescope assignment containers.

    Downloads `csv.zip` (a Version Set Scores zip) for each ASSIGNMENT_CONTAINER ID
    provided and saves the files to SAVE_DIR (or the current directory if omitted).
    """
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    failed = False
    for container in assignment_containers:
        try:
            paths = client.save_assignment_container_scores(
                course=course,
                assignment_container=container,
                save_dir=save_dir,
                headless=headless,
            )
            for p in paths:
                typer.echo(f"Container scores saved to {p}")
        except Exception as e:
            typer.echo(
                f"Failed to download container scores for assignment_container {container}: {e}",
                err=True,
            )
            failed = True
    if failed:
        raise typer.Exit(code=1)


@client_app.command("list-versions")
def list_versions(
    course: Annotated[str, typer.Argument(help="Gradescope course ID")],
    assignment_container: Annotated[
        str, typer.Argument(help="Gradescope assignment container ID")
    ],
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """List the version assignments of an assignment container (version set)."""
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    for v in client.list_container_versions(course, assignment_container, headless=headless):
        typer.echo(
            f"{v['id']}\t{v['title']}\t{v['num_active_submissions']} submissions\t"
            f"{v['grading_progress']}% graded"
        )


@client_app.command("grading-snapshot")
def grading_snapshot(
    course: Annotated[str, typer.Argument(help="Gradescope course ID")],
    assignment_containers: Annotated[
        List[str],
        typer.Argument(help="One or more Gradescope assignment container IDs"),
    ],
    save_dir: Annotated[
        Path, typer.Option(help="Target directory for dashboards.json and scores CSVs")
    ] = Path("."),
    scores: Annotated[
        bool,
        typer.Option("--scores/--no-scores", help="Also download each version's scores CSV"),
    ] = True,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Save per-question grading progress (and scores) of assignment containers.

    Reads the Grading Dashboard of every version of each ASSIGNMENT_CONTAINER into
    SAVE_DIR/dashboards.json, and downloads each version's scores CSV.
    """
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    path = client.save_grading_snapshot(
        course, assignment_containers, save_dir, scores=scores, headless=headless
    )
    typer.echo(f"Grading snapshot saved to {path}")


@client_app.command("export-submissions")
def export_submissions(
    course: Annotated[str, typer.Argument(help="Gradescope course ID")],
    ids: Annotated[
        List[str],
        typer.Argument(help="Assignment IDs, or assignment container IDs with --container"),
    ],
    container: Annotated[
        bool, typer.Option("--container", help="IDS are version sets: export every version")
    ] = False,
    save_dir: Annotated[Path, typer.Option(help="Target directory")] = Path("."),
    reuse: Annotated[
        bool,
        typer.Option("--reuse", help="Download an existing completed export instead of starting a new one"),
    ] = False,
    extract: Annotated[
        bool, typer.Option("--extract/--no-extract", help="Unzip into SAVE_DIR and delete the zip")
    ] = True,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for each export")] = 600,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Export submissions (PDFs + submission_metadata.yml) of assignments and download them."""
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    options = dict(fresh=not reuse, extract=extract, timeout=timeout, headless=headless)
    paths: list[Path] = []
    for item in ids:
        if container:
            paths += client.export_container_submissions(course, item, save_dir, **options)
        else:
            paths += client.save_submissions_export(course, [item], save_dir, **options)
    for path in paths:
        typer.echo(f"Submissions export saved to {path}")


@client_app.command("upload-scans")
def upload_scans(
    course: Annotated[str, typer.Argument(help="Gradescope course ID")],
    assignment: Annotated[
        str, typer.Argument(help="Assignment ID, or assignment container ID with --container")
    ],
    pdf_paths: Annotated[
        List[Path],
        typer.Argument(help="Scanned PDFs to upload", exists=True, dir_okay=False, readable=True),
    ],
    container: Annotated[
        bool, typer.Option("--container", help="ASSIGNMENT is a version set's container")
    ] = False,
    allow_duplicates: Annotated[
        bool,
        typer.Option("--allow-duplicates", help="Upload a PDF even if one of the same name was uploaded"),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option("--wait/--no-wait", help="Wait for Gradescope to split the scans into submissions"),
    ] = True,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for the uploads")] = 900,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Check the page and for duplicates, but upload nothing")
    ] = False,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Upload scanned PDFs on an assignment's Manage Scans page.

    Gradescope splits each PDF into submissions and matches them to students.
    Scans it couldn't split automatically need "Show" and "Create Submissions"
    on the Manage Scans page.
    """
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        results = client.upload_scans(
            course=course,
            assignment=assignment,
            pdf_paths=pdf_paths,
            container=container,
            allow_duplicates=allow_duplicates,
            wait=wait,
            timeout=timeout,
            dry_run=dry_run,
            headless=headless,
        )
    except Exception as e:
        typer.echo(f"Failed to upload scans to {assignment} in course {course}: {e}", err=True)
        raise typer.Exit(code=1) from e

    if dry_run:
        names = ", ".join(r["file"] for r in results)
        typer.echo(f"Dry run: ready to upload {names}; nothing uploaded.")
        return
    for r in results:
        typer.echo(
            f"{r['file']}: {r['pages']} pages, {r['status']}, "
            f"{r['submissions']} submissions (batch {r['batch']})"
        )
        if r["status"] != "was_automatically_split":
            typer.echo(f"  Review this batch on the Manage Scans page: {r['status']}")


@client_app.command("replace-pdf")
def replace_pdf(
    course: Annotated[str, typer.Argument(help="Gradescope course ID")],
    assignment: Annotated[
        str, typer.Argument(help="Gradescope assignment ID, or its title (e.g. 'Problem Set 7')")
    ],
    pdf_path: Annotated[
        Path, typer.Argument(help="The new template PDF", exists=True, dir_okay=False, readable=True)
    ],
    exam_ok: Annotated[
        bool,
        typer.Option(
            "--exam-ok",
            help="Replace the PDF of an Exam/Quiz, whose question regions you must then check by hand",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Find the form controls but submit nothing")
    ] = False,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Gradescope base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Replace an assignment's template PDF, then re-save its outline.

    ASSIGNMENT may be an ID or the assignment's title, which is matched
    ignoring case and must be unique in the course.

    A Problem Set (variable length) has no question regions, so this is safe.
    An Exam/Quiz (templated) has regions drawn on the template that a new PDF
    can leave misplaced: it is refused unless --exam-ok is given.
    """
    client = GradescopeClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        result = client.replace_template_pdf(
            course=course,
            assignment=assignment,
            pdf_path=pdf_path,
            allow_region_shift=exam_ok,
            dry_run=dry_run,
            headless=headless,
        )
    except Exception as e:
        typer.echo(f"Failed to replace PDF for assignment '{assignment}' in course {course}: {e}", err=True)
        raise typer.Exit(code=1) from e

    label = f"'{result['title']}' (assignment {result['assignment']}, {result['type']})"
    if result["dry_run"]:
        typer.echo(f"Dry run: ready to replace the PDF of {label} with {pdf_path.name}; nothing submitted.")
        if result["regions_need_review"]:
            typer.echo("A real run needs --exam-ok: " + REGION_WARNING + ".")
        return
    typer.echo(f"Replaced the PDF of {label} with {pdf_path.name} and saved the outline.")
    if result["regions_need_review"]:
        typer.echo(f"Warning: {REGION_WARNING}: {result['outline_url']}")


# Register the gradescope app as a subcommand with the main app
main_app.add_typer(app, name="gradescope")
app.add_typer(client_app, name="client")
