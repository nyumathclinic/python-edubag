from pathlib import Path
import re
import typer
from typing import Iterator, IO, Optional, Union, Annotated
import zipfile

from loguru import logger
import pandas as pd

class Scoresheet(object):
    """Class representing a scoresheet for a Gradescope assignment."""

    name: str
    scores: pd.DataFrame

    def __init__(self, name: str, scores: pd.DataFrame):
        self.name = name
        self.scores = scores

    @classmethod
    def from_csv(
        cls,
        csv_source: Annotated[
            Union[str, Path, IO[bytes], bytes],
            typer.Argument(help="Path to the CSV file or a file-like object."),
        ],
        drop_missing: Annotated[
            bool, typer.Option(help="Drop rows with Status == 'Missing'.")
        ] = True,
        *,
        filename: Annotated[
            Optional[str],
            typer.Option(help="Original filename to derive the scoresheet name"),
        ] = None,
    ) -> "Scoresheet":
        """Creates a Scoresheet object from a CSV file or file-like.

        Reads the CSV (path/str/bytes buffer) and parses it into a pandas DataFrame.
        Casts the columns to the appropriate data types.

        """
        df = pd.read_csv(
            csv_source,
            dtype={
                "Submission ID": "Int64",
                "First Name": "string",
                "Last Name": "string",
                "Email": "string",
                "Status": "category",
                "Submission Count": "Int64",
                "View Count": "Int64",
                "Sections": "string",
            },
            parse_dates=["Submission Time"],
        )
        # df['Total Percent'] = df['Total Score'] / df['Max Points'] * 100
        if drop_missing:
            df.drop(df[df["Status"] == "Missing"].index, inplace=True)
        # Derive a friendly name from the filename when available
        source_name: Optional[str]
        if filename is not None:
            source_name = filename
        elif isinstance(csv_source, (str, Path)):
            source_name = str(csv_source)
        else:
            source_name = None

        if source_name:
            base = Path(source_name).name
            # Typical pattern: "Assignment_Name_scores.csv"
            name = base
            if base.endswith("_scores.csv"):
                name = base[: -len("_scores.csv")]
            elif base.endswith(".csv"):
                name = base[: -len(".csv")]
            name = name.replace("_", " ")
        else:
            name = "Scoresheet"
        return cls(name=name, scores=df)


def version_csvs_from(zipfile_path: Path) -> Iterator[Path]:
    """Finds the version CSV files for a Gradescope version set Zip file

    Args:
        zipfile_path: The path to the directory containing the CSV files.

    Yields: pathlib.Path
        Paths for the CSV files
    """

    with zipfile.ZipFile(zipfile_path, "r") as zip_ref:
        for member in zip_ref.infolist():
            if (
                re.search(r"\.csv$", member.filename)
                and not re.search(r"_Set_Scores\.csv", member.filename)
                and not re.search(r"_Unassigned_scores.csv", member.filename)
            ):
                yield Path(member.filename)

class SectionedScoresheet(Scoresheet):
    """Class representing a scoresheet with section assignments."""

    def __init__(self, name: str, scores: pd.DataFrame):
        self.name = name
        self.scores = scores

    def by_section(self) -> dict[str, "SectionedScoresheet"]:
        """Splits the scoresheet into separate scoresheets for each section.

        Returns:
            A dictionary mapping section names to SectionedScoresheet objects.

        Note:
            Students without section assignments (NaN) are skipped.
        """
        sections = self.scores["Sections"].unique()
        sectioned_scoresheets = {}
        # warn about students without section assignments
        if pd.isna(sections).any():
            unassigned = self.scores[self.scores["Sections"].isna()]
            logger.warning(
                f"Skipping {len(unassigned)} student(s) without section assignment: "
                f"{unassigned['Email'].tolist()}"
            )
        for section in sections:
            # Skip NaN sections (students without section assignments)
            if pd.isna(section):
                continue
            section_scores = self.scores[self.scores["Sections"] == section]
            sectioned_scoresheets[section] = SectionedScoresheet(
                name=self.name,
                scores=section_scores,
            )
        return sectioned_scoresheets
    


class VersionedScoresheet(Scoresheet):
    """Class representing a scoresheet for a versioned Gradescope assignment."""

    def __init__(self, name: str, scores: pd.DataFrame):
        self.name = name
        self.scores = scores

    @classmethod
    def from_zip(cls, zip_path: Path, drop_missing: bool = True):
        """Creates a VersionedScoresheet object from a Zip file.

        Reads each CSV file and parses it into a pandas DataFrame.
        Casts the columns to the appropriate data types.

        Every column of every version CSV is kept, even one that is empty in all of them
        (e.g. a question nobody has graded yet).

        Args:
            zip_path (Path): file handle to the Zip file.
            drop_missing: Drop rows with Status == 'Missing'.
        Returns:
            A VersionedScoresheet object containing the scoresheet data.
        """
        name = zip_path.name[: zip_path.name.rfind("_Version_Set_Scores.zip")].replace(
            "_", " "
        )
        frames = []
        columns: list[str] = []
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            for csv_path in version_csvs_from(zip_path):
                with zip_ref.open(str(csv_path)) as f:
                    ss = Scoresheet.from_csv(
                        f, drop_missing=drop_missing, filename=str(csv_path)
                    )
                    version_df = ss.scores.copy()
                    columns += [c for c in version_df.columns if c not in columns]
                    # Drop all-NA columns before concat to keep pandas dtype
                    # inference stable across versions.
                    version_df = version_df.dropna(axis=1, how="all").copy()

                    # Avoid pandas concat FutureWarning for empty/all-NA entries.
                    if version_df.empty or version_df.dropna(axis=0, how="all").empty:
                        continue

                    version_df["Version"] = ss.name
                    frames.append(version_df)

        if frames:
            df = pd.concat(frames, ignore_index=True)
            # Restore the all-NA columns dropped above, in their original order.
            df = df.reindex(columns=columns + ["Version"])
        else:
            df = pd.DataFrame(columns=columns + ["Version"])
        return cls(name=name, scores=df)


# Gradescope names a question's score column "<outline number>: <title> (<points> pts)".
QUESTION_COLUMN_RE = re.compile(
    r"^(?P<number>\d+(?:\.\d+)*): (?P<title>.*) \((?P<points>\d+(?:\.\d+)?) pts\)$"
)


def parse_question_column(column: str) -> Optional[tuple[str, str, float]]:
    """Parse a scores CSV question column into (outline number, title, max points).

    Returns None if the column is not a question column.
    """
    match = QUESTION_COLUMN_RE.match(str(column))
    if match is None:
        return None
    return match["number"], match["title"], float(match["points"])


def question_columns(df: pd.DataFrame) -> dict[str, str]:
    """Map outline number ("1", "2.1", ...) to column name for a scores DataFrame's questions.

    Raises:
        ValueError: If two columns have the same outline number (e.g. a DataFrame that mixes
            versions whose questions have different titles); split it by version first.
    """
    result: dict[str, str] = {}
    for column in df.columns:
        parsed = parse_question_column(column)
        if parsed is None:
            continue
        number = parsed[0]
        if number in result:
            raise ValueError(
                f"Question {number} has more than one column ({result[number]!r}, {column!r})"
            )
        result[number] = column
    return result
