import re
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
import typer
import yaml
from loguru import logger

from edubag import app as main_app
from edubag.aggregator import EngagementAggregator
from edubag.brightspace.attendance import AttendanceData
from edubag.brightspace.gradebook import Gradebook
from edubag.edstem.analytics import EdstemAnalytics
from edubag.sources import OfficeHoursData
from edubag.transformers import GradebookTransformer

from .client import BrightspaceClient

# Create brightspace subcommands app
app = typer.Typer(help="Brightspace management commands")


# Gradebook now inherits DataSource; the previous wrapper is no longer needed.


@app.command("build-attendance")
def build_attendance_gradebook(
    attendance_dir: Annotated[
        Path,
        typer.Argument(help="Directory containing Brightspace attendance CSV files"),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            help="Output Brightspace gradebook CSV path. If omitted, writes to "
            "`data/processed/brightspace/grades/<today>/attendance.csv`"
        ),
    ] = None,
    column_name: Annotated[
        str,
        typer.Option(help="Name of the Brightspace grade item column to populate"),
    ] = "Attendance Points",
    scale: Annotated[
        float,
        typer.Option(
            help="Scale factor applied to attendance points (e.g., weight per session)"
        ),
    ] = 1.0,
):
    """Aggregate attendance CSVs and produce a Brightspace-ready gradebook CSV.

    - Detects `Username` (or derives from `Email` by stripping domain).
    - Sums numeric `Points` if present; else counts `Present`/`Present (Excused)` statuses.
    - Applies optional scaling, then writes a gradebook CSV suitable for Brightspace import.
    """

    try:
        gb = Gradebook.from_attendance_dir(
            attendance_dir, column_name=column_name, scale=scale
        )
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e

    if output is None:
        today = pd.Timestamp.today().strftime("%Y-%m-%d")
        output = attendance_dir / f"attendance_gradebook_{today}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Writing Brightspace attendance gradebook to {output}")
    gb.to_csv(output)
    logger.info("Done.")


@app.command("build-gradebook")
def build_engagement_gradebook(
    base_gradebook: Annotated[
        Path,
        typer.Argument(help="Path to base Brightspace gradebook CSV export"),
    ],
    attendance_file: Annotated[
        Path | None,
        typer.Option(help="Path to Brightspace attendance CSV file"),
    ] = None,
    edstem_file: Annotated[
        Path | None,
        typer.Option(help="Path to EdSTEM analytics CSV file"),
    ] = None,
    office_hours_file: Annotated[
        Path | None,
        typer.Option(help="Path to office hours log (HTML, ZIP, or CSV)"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(help="Output Brightspace gradebook CSV path"),
    ] = None,
    attendance_scale: Annotated[
        float,
        typer.Option(help="Scale factor for attendance points (max points)"),
    ] = 10.0,
    edstem_scale: Annotated[
        float,
        typer.Option(help="Scale factor for EdSTEM engagement points (max points)"),
    ] = 10.0,
    office_hours_scale: Annotated[
        float,
        typer.Option(help="Points per office hours visit"),
    ] = 1.0,
    show_report: Annotated[
        bool,
        typer.Option(help="Print validation report after aggregation"),
    ] = True,
    categories_to_analyze: Annotated[
        str | None,
        typer.Option(
            help=(
                "Comma-separated list of grade categories to analyze "
                "(e.g., 'Pre-Quizzes,Pre-Surveys,Polls'). If not specified, will auto-detect all categories."
            ),
        ),
    ] = None,
):
    """Build an engagement-enhanced gradebook from multiple data sources.

	This command:
	1. Loads the base Brightspace gradebook
	2. Loads optional data sources (attendance, EdSTEM, office hours)
	3. Merges them on Username with identity resolution
	4. Computes engagement columns based on configured formulas
	5. Validates and reports changes
	6. Writes an updated Brightspace-ready gradebook CSV

	Example:
		python -m edubag brightspace build-gradebook \\
			data/raw/brightspace/grades/2025-12-18/gradebook.csv \\
			--attendance-file data/raw/brightspace/attendance/2025-12-18/attendance.csv \\
			--edstem-file data/raw/edstem/analytics/2025-12-18/analytics.csv \\
			--office-hours-file data/raw/mpl5/ohlog/2025-12-18/log.html \\
			--output data/processed/brightspace/grades/2025-12-18/gradebook_with_engagement.csv
	"""
    # Load base gradebook
    logger.info(f"Loading base gradebook from {base_gradebook}")
    base_gb = Gradebook.from_csv(base_gradebook)
    logger.info(
        f"Base gradebook: {len(base_gb.grades)} students, {len(base_gb.grades.columns)} columns"
    )

    # Apply GradebookTransformer to compute category metrics
    transformer = GradebookTransformer(base_gb)
    if transformer.category_columns:
        if categories_to_analyze:
            categories = [c.strip() for c in categories_to_analyze.split(",")]
            categories = [c for c in categories if c in transformer.category_columns]
        else:
            categories = list(transformer.category_columns.keys())
        if categories:
            logger.info(f"Computing category metrics for: {', '.join(categories)}")
            transformer.add_category_metrics(categories)
            category_metadata = transformer.get_metadata()
            logger.info(f"Computed metrics for {len(categories)} categories")
        else:
            logger.warning("No valid categories found")
            category_metadata = {}
    else:
        logger.info("No grade categories found in base gradebook")
        category_metadata = {}

    # Initialize aggregator
    aggregator = EngagementAggregator(base_gradebook=base_gb)

    # Add base gradebook directly as a source (Gradebook is a DataSource)
    if category_metadata:
        base_gb.resolve_identity()
        aggregator.add_source("gradebook", base_gb)
        logger.info("Added gradebook (category metrics) as a source")

    # Configure engagement columns
    engagement_config = {}

    # Add category metrics (derived from base gradebook)
    for category, meta in category_metadata.items():
        total_items = meta["total_items"]
        engagement_config[f"{category} Completion Rate"] = {
            "source": "gradebook",
            "column": f"{category}_positive",
            "scale": 100.0 / max(total_items, 1),  # Convert to percentage
        }

    # Load and add data sources
    if attendance_file and attendance_file.exists():
        logger.info(f"Loading attendance data from {attendance_file}")
        att = AttendanceData.from_file(attendance_file)
        att.resolve_identity()
        aggregator.add_source("attendance", att)

        engagement_config["Attendance Points"] = {
            "source": "attendance",
            "column": "% Attendance",
            "scale": attendance_scale,
        }

    if edstem_file and edstem_file.exists():
        logger.info(f"Loading EdSTEM analytics from {edstem_file}")
        edstem = EdstemAnalytics.from_file(edstem_file)
        edstem.resolve_identity()
        aggregator.add_source("edstem", edstem)

        engagement_config["EdSTEM Points"] = {
            "formula": "Posts * 0.5 + Answers * 1.0 + Reactions * 0.1",
            "scale": 1.0,
            "clip_upper": edstem_scale,
        }

    if office_hours_file and office_hours_file.exists():
        logger.info(f"Loading office hours log from {office_hours_file}")
        ohours = OfficeHoursData.from_file(office_hours_file)
        ohours.resolve_identity()
        aggregator.add_source("office_hours", ohours)

        engagement_config["Office Hours Points"] = {
            "source": "office_hours",
            "column": "visit_count",
            "scale": office_hours_scale,
        }

    if not aggregator.sources:
        raise typer.BadParameter(
            "At least one data source must be provided (attendance, edstem, or office_hours)"
        )

    # Set configuration
    aggregator.config = engagement_config
    logger.info(f"Configured {len(engagement_config)} engagement columns")

    # Merge and compute
    logger.info("Merging data sources...")
    aggregator.merge_sources()

    logger.info("Computing engagement columns...")
    aggregator.compute_columns()

    # Add intermediate denominator components for the unified engagement score
    merged_df = aggregator.merged_data

    # Pre-quizzes denominator: total - exemptions (from metadata)
    pq_total = category_metadata.get("Pre-Quizzes", {}).get("total_items", 0)
    if pq_total > 0:
        merged_df["pq_denominator"] = pq_total - merged_df.get(
            "gradebook_Pre-Quizzes_exemptions", 0
        )

    # Pre-surveys denominator: total - exemptions
    ps_total = category_metadata.get("Pre-Surveys", {}).get("total_items", 0)
    if ps_total > 0:
        merged_df["ps_denominator"] = ps_total - merged_df.get(
            "gradebook_Pre-Surveys_exemptions", 0
        )

    # Polls denominator: total - exemptions
    pl_total = category_metadata.get("Polls", {}).get("total_items", 0)
    if pl_total > 0:
        merged_df["pl_denominator"] = pl_total - merged_df.get(
            "gradebook_Polls_exemptions", 0
        )

    # Attendance denominator: total sessions - X count
    # (attendance records have P, R, A, X counts; total sessions = P + R + A + X)
    if "attendance_P" in merged_df.columns:
        merged_df["att_total_sessions"] = (
            merged_df.get("attendance_P", 0)
            + merged_df.get("attendance_R", 0)
            + merged_df.get("attendance_A", 0)
            + merged_df.get("attendance_X", 0)
        )
        merged_df["att_denominator"] = merged_df["att_total_sessions"] - merged_df.get(
            "attendance_X", 0
        )

    logger.info("Added intermediate denominator columns")

    # Validate
    logger.info("Validating aggregated data...")
    aggregator.validate()

    # Show report if requested
    if show_report:
        aggregator.print_report()

    # Convert to gradebook
    output_gb = aggregator.to_gradebook(keep_source_columns=False)

    # Write output
    if output is None:
        today = pd.Timestamp.today().strftime("%Y-%m-%d")
        output = base_gradebook.parent / f"gradebook_with_engagement_{today}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Writing engagement gradebook to {output}")
    output_gb.to_csv(output)
    logger.success(
        f"✅ Wrote gradebook with {len(output_gb.grades)} students to {output}"
    )


# Config-driven aggregation helpers


def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    """Load configuration from YAML file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def _resolve_config_path(path_str: str, base_dir: Path) -> Path:
    """Resolve a path string to an absolute path."""
    path = Path(path_str)
    if not path.is_absolute():
        path = base_dir / path
    return path


def _load_data_source_from_config(name: str, source_config: dict, base_dir: Path):
    """Load a data source based on configuration."""
    path = _resolve_config_path(source_config["path"], base_dir)
    source_type = source_config["type"]

    logger.info(f"Loading {name} ({source_type}) from {path}")

    if source_type == "brightspace_gradebook":
        source = Gradebook.from_csv(path)
        source.resolve_identity()
        return source, source_config.get("categories", [])
    elif source_type == "attendance":
        source = AttendanceData.from_file(path)
        source.resolve_identity()
        return source, []
    elif source_type == "edstem_analytics":
        source = EdstemAnalytics.from_file(path)
        source.resolve_identity()
        return source, []
    elif source_type == "office_hours_html":
        source = OfficeHoursData.from_file(path)
        source.resolve_identity()
        return source, []
    else:
        raise ValueError(f"Unknown source type: {source_type}")


def _build_formula_string(components: list, merged_df: pd.DataFrame) -> str:
    """Build a formula string from components."""
    return " + ".join(components)


def _apply_piecewise_mapping(base_col: str, piecewise_config: list) -> str:
    """Build a piecewise formula string."""
    formula_parts = []
    for piece in piecewise_config:
        condition = piece["condition"]
        formula_expr = piece["formula"].replace("x", f"`{base_col}`")

        # Convert condition to pandas eval syntax
        if "<=" in condition:
            cond_expr = f"(`{base_col}` {condition})"
        elif ">=" in condition:
            cond_expr = f"(`{base_col}` {condition})"
        elif "and" in condition:
            # Split compound condition
            parts = condition.split(" and ")
            cond_expr = f"(`{base_col}` {parts[0]}) * (`{base_col}` {parts[1]})"
        else:
            cond_expr = f"(`{base_col}` {condition})"

        formula_parts.append(f"({cond_expr} * {formula_expr})")

    return " + ".join(formula_parts)


@app.command("aggregate-from-config")
def aggregate_from_config(
    config_file: Annotated[
        Path,
        typer.Argument(help="Path to YAML configuration file"),
    ],
    output: Annotated[
        Path | None,
        typer.Option(help="Override output path from config file"),
    ] = None,
    show_report: Annotated[
        bool,
        typer.Option(help="Print validation report after aggregation"),
    ] = True,
):
    """Compute engagement scores using a YAML configuration file.

	This command uses a declarative YAML configuration to specify:
	- Data source paths and types
	- Denominator formulas
	- Engagement column definitions
	- Piecewise adjustments
	- Validation thresholds

	This approach separates "what" (configuration) from "how" (code),
	making it easier to:
	- Update paths and formulas without modifying code
	- Maintain different configurations for different terms
	- Version control configurations separately
	- Allow non-programmers to modify scoring logic

	Example:
		python -m edubag brightspace aggregate-from-config \\
			config/engagement_score.yaml

	See config/README.md for configuration format documentation.
	"""
    # Load configuration
    logger.info(f"Loading configuration from {config_file}")
    cfg = _load_yaml_config(config_file)

    # Determine base directory for relative paths
    base_dir = config_file.parent.resolve()

    # Load data sources
    logger.info("\n📚 Loading data sources...")
    sources = {}
    gradebook_categories = []
    base_gradebook = None

    for name, source_config in cfg["data_sources"].items():
        source, categories = _load_data_source_from_config(
            name, source_config, base_dir
        )
        sources[name] = source

        if name == "gradebook":
            base_gradebook = source
            gradebook_categories = categories
            # Drop pre-existing engagement columns
            drop_cols = [
                c
                for c in source.grades.columns
                if c.startswith("Engagement Raw Score Points")
                or c.startswith("Engagement Adjusted Score Points")
            ]
            if drop_cols:
                source.grades = source.grades.drop(columns=drop_cols)
                source.data = source.grades.copy()

    # Initialize aggregator (with or without base gradebook)
    logger.info("\n🔧 Setting up aggregator...")
    aggregator = EngagementAggregator(base_gradebook=base_gradebook)

    # If gradebook exists, apply transformations
    category_metadata = {}
    if base_gradebook is not None:
        logger.info("📊 Computing category metrics...")
        transformer = GradebookTransformer(base_gradebook)
        if gradebook_categories:
            transformer.add_category_metrics(gradebook_categories)
            category_metadata = transformer.get_metadata()
            for cat, meta in category_metadata.items():
                logger.info(f"  {cat}: {meta['total_items']} items")
        else:
            logger.info("  No categories specified or found in gradebook")

    # Add sources
    for name, source in sources.items():
        aggregator.add_source(name, source)

    # Merge sources
    logger.info("\n🔗 Merging sources...")
    aggregator.merge_sources()
    merged_df = aggregator.merged_data

    # Compute denominator components
    logger.info("\n📐 Computing denominators...")
    for denom_name, denom_config in cfg.get("denominators", {}).items():
        formula = denom_config["formula"]

        # Replace category metadata placeholders
        if "category_metadata" in formula:
            for cat in category_metadata:
                pattern = f"category_metadata['{cat}']['total_items']"
                if pattern in formula:
                    formula = formula.replace(
                        pattern, str(category_metadata[cat]["total_items"])
                    )

        # Support backticked column names (e.g., `gradebook_Pre-Quizzes_exemptions`)
        formula_eval = formula
        if "`" in formula_eval:
            formula_eval = re.sub(
                r"`([^`]+)`", lambda m: f"df['{m.group(1)}']", formula_eval
            )

        logger.debug(
            f"  Columns in merged_df: {[c for c in merged_df.columns if 'exemptions' in c or 'denominator' in c]}"
        )

        # Replace bare column names that are valid identifiers with df['col']
        # BUT: skip 'df' itself and any column names that are already wrapped in df[...]
        for col in merged_df.columns:
            if col == "Username" or col == "df":
                continue
            if re.match(r"^[A-Za-z_]\w*$", col):
                # Don't replace if it's already wrapped in df[...]
                pattern = r"\b" + re.escape(col) + r"(?!\])"
                if re.search(pattern, formula_eval):
                    # Only replace if not already in df[...]
                    if (
                        f"df['{col}']" not in formula_eval
                        and f'df["{col}"]' not in formula_eval
                    ):
                        formula_eval = re.sub(pattern, f"df['{col}']", formula_eval)

        # Evaluate formula in Python to handle bracketed column access
        logger.debug(f"  Denominator formula for {denom_name}: {formula_eval}")
        try:
            merged_df[denom_name] = eval(
                formula_eval,
                {},
                {"df": merged_df, "category_metadata": category_metadata},
            )
            logger.info(f"  Computed {denom_name}")
        except Exception as e:
            logger.error(f"  Failed to compute {denom_name}: {e}")

    # Build aggregator config from YAML columns
    logger.info("\n⚙️  Building column configurations...")
    aggregator_config = {}

    for col_name, col_config in cfg["columns"].items():
        logger.info(f"  Configuring {col_name}")

        if "piecewise" in col_config:
            # Piecewise mapping from base column
            base_col = col_config["base_column"]
            formula = _apply_piecewise_mapping(base_col, col_config["piecewise"])
        elif "numerator" in col_config:
            # Formula from numerator/denominator
            numerator = _build_formula_string(col_config["numerator"], merged_df)
            denominator = col_config["denominator"]
            formula = f"({numerator}) / ({denominator})"
        else:
            logger.warning(f"  No formula specification for {col_name}")
            continue

        aggregator_config[col_name] = {
            "formula": formula,
            "scale": col_config.get("scale", 1.0),
            "clip_upper": col_config.get("clip_upper", None),
            "clip_lower": col_config.get("clip_lower", None),
        }

    aggregator.config = aggregator_config

    # Compute columns
    logger.info("\n🧮 Computing engagement columns...")
    aggregator.compute_columns()

    # Validate
    logger.info("\n✅ Validating...")
    report = aggregator.validate()

    # Check validation thresholds
    validation_config = cfg.get("validation", {})
    for col_name, stats in report.get("column_stats", {}).items():
        zero_threshold = validation_config.get("warn_zero_percent_threshold", 50)
        if stats["count"] > 0:
            zero_pct = (stats["zeros"] / stats["count"]) * 100
            if zero_pct > zero_threshold:
                logger.warning(
                    f"  {col_name}: {zero_pct:.1f}% zeros (threshold: {zero_threshold}%)"
                )

    # Show report if requested
    if show_report:
        aggregator.print_report()

    # Export
    logger.info("\n📤 Exporting...")
    output_gb = aggregator.to_gradebook(keep_source_columns=False)

    # Filter columns if configured
    output_config = cfg.get("output", {})
    if output_config.get("keep_only_engagement_columns", False):
        engagement_cols = list(cfg["columns"].keys())
        keep_cols = ["Username"] + [
            c for c in engagement_cols if c in output_gb.grades.columns
        ]
        output_gb.grades = output_gb.grades[keep_cols]

    # Write output
    if output is None:
        output = _resolve_config_path(output_config["path"], base_dir)
    output.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Writing engagement gradebook to {output}")
    output_gb.to_csv(output)
    logger.success(
        f"✅ Wrote gradebook with {len(output_gb.grades)} students to {output}"
    )

    # Show sample if configured
    display_config = cfg.get("display", {})
    sample_rows = display_config.get("sample_rows", 0)
    if sample_rows > 0:
        logger.info(f"\n📊 Sample engagement scores (first {sample_rows} rows):")
        df = pd.read_csv(output, nrows=sample_rows)
        print(df.to_string(index=False))


# Nested Typer app for web client automation
client_app = typer.Typer(help="Automate Brightspace web client interactions")


@client_app.command()
def authenticate(
    base_url: Annotated[
        str | None, typer.Option(help="Override Brightspace base URL")
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
    """Open Brightspace for login and persist authentication state."""
    client = BrightspaceClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        client.authenticate(headless=headless)
        typer.echo("Authentication state saved.")
    except Exception as e:
        typer.echo(f"Authentication failed: {e}", err=True)
        raise typer.Exit(code=1)


@client_app.command("save-gradebook")
def save_gradebook(
    course: Annotated[str, typer.Argument(help="Course ID or full URL to the course")],
    save_dir: Annotated[
        Path | None, typer.Option(help="Directory to save the gradebook file")
    ] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Brightspace base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Fetch and save the gradebook for a course."""
    client = BrightspaceClient(base_url=base_url, auth_state_path=auth_state_path)
    paths = client.save_gradebook(
        course=course,
        save_dir=save_dir,
        headless=headless,
    )
    for p in paths:
        typer.echo(str(p))


@client_app.command("save-attendance")
def save_attendance(
    course: Annotated[str, typer.Argument(help="Course ID or full URL to the course")],
    save_dir: Annotated[
        Path | None, typer.Option(help="Directory to save the attendance files")
    ] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Brightspace base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Fetch and save the attendance registers for a course."""
    client = BrightspaceClient(base_url=base_url, auth_state_path=auth_state_path)
    paths = client.save_attendance(
        course=course,
        save_dir=save_dir,
        headless=headless,
    )
    for p in paths:
        typer.echo(str(p))


@client_app.command("save-gradebook-structure")
def save_gradebook_structure(
    course: Annotated[str, typer.Argument(help="Course ID (ou)")],
    save_dir: Annotated[
        Path | None, typer.Option(help="Directory to save the JSON file")
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(help="Only crawl the first N grade objects (for testing)"),
    ] = None,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Brightspace base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Save every grade object's settings (no grades) from Manage Grades as JSON."""
    client = BrightspaceClient(base_url=base_url, auth_state_path=auth_state_path)
    paths = client.save_gradebook_structure(
        course=course,
        save_dir=save_dir,
        headless=headless,
        limit=limit,
    )
    for p in paths:
        typer.echo(str(p))


@client_app.command("set-start-dates")
def set_start_dates(
    course: Annotated[str, typer.Argument(help="Course ID (ou)")],
    schedule_file: Annotated[
        Path,
        typer.Argument(help='JSON file mapping dates to grade item IDs, e.g. {"2026-09-03": 1926725}'),
    ],
    at: Annotated[
        str, typer.Option("--time", help="Start time (24-hour HH:MM, course local time)")
    ] = "12:00",
    only: Annotated[
        list[int] | None,
        typer.Option(help="Only update this grade item ID (repeatable)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(help="Fill in each Restrictions form without saving"),
    ] = False,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Brightspace base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Set each grade item's start date to the Friday after its listed date.

    Example:
        python -m edubag brightspace client set-start-dates 611388 polls.json --only 1940969 --headed
    """
    from datetime import date, datetime

    from edubag.brightspace.manage_grades import following_weekday

    try:
        start_time = datetime.strptime(at, "%H:%M").time()
        mapping = yaml.safe_load(schedule_file.read_text())
        schedule = {
            int(item_id): following_weekday(date.fromisoformat(str(day)), at=start_time)
            for day, item_id in mapping.items()
        }
    except (ValueError, AttributeError) as e:
        raise typer.BadParameter(str(e)) from e
    if only:
        missing = set(only) - set(schedule)
        if missing:
            raise typer.BadParameter(f"Not in {schedule_file}: {sorted(missing)}")
        schedule = {item_id: start for item_id, start in schedule.items() if item_id in only}

    client = BrightspaceClient(base_url=base_url, auth_state_path=auth_state_path)
    results = client.set_start_dates(course, schedule, headless=headless, dry_run=dry_run)
    for item_id, r in results.items():
        before = (r.get("before") or {}).get("value") or "none"
        typer.echo(f"{r['status']:>9}  {item_id}  {r.get('name', '')}: {before} -> {r['target']}"
                   + (f"  ({r['error']})" if "error" in r else ""))
    if any(r["status"] == "failed" for r in results.values()):
        raise typer.Exit(code=1)


@client_app.command("clear-grades")
def clear_grades(
    course: Annotated[
        str,
        typer.Argument(help="Course ID (ou) or full Enter Grades URL for the grade item"),
    ],
    grade_items: Annotated[
        list[str] | None,
        typer.Argument(help="One or more grade item IDs (objectId); omit when passing a full URL"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(help="Select all rows and find 'Clear Grades' without clicking it"),
    ] = False,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless/--headed",
            help="Run browser headless (for automation) or headed (for debugging)",
        ),
    ] = True,
    base_url: Annotated[
        str | None, typer.Option(help="Override Brightspace base URL")
    ] = None,
    auth_state_path: Annotated[
        Path | None, typer.Option(help="Path to stored auth state JSON")
    ] = None,
) -> None:
    """Clear all grades for one or more grade items and save.

    Example:
        python -m edubag brightspace client clear-grades 611388 1940969 1940935
    """
    client = BrightspaceClient(base_url=base_url, auth_state_path=auth_state_path)
    try:
        failures = client.clear_grades(
            course=course,
            grade_items=grade_items or [],
            headless=headless,
            dry_run=dry_run,
        )
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if failures:
        typer.echo(f"Failed to clear {len(failures)} grade item(s):", err=True)
        for url, error in failures.items():
            typer.echo(f"  {url}: {error}", err=True)
        raise typer.Exit(code=1)


# Register the brightspace app as a subcommand with the main app
main_app.add_typer(app, name="brightspace")
app.add_typer(client_app, name="client")
