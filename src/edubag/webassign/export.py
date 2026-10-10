"""Parse WebAssign Download Manager exports (rosters and scores).

A WebAssign CSV export is several blocks of rows separated by blank rows
(written as ``""``):

* Course block: the class title (``"MATH-UA 122, section 016+021, Fall 2026"``),
  the instructor, and the download time.
* Assignment block (scores only): a transposed table. The ``Assignment Name``
  row names each assignment column, after a ``Total`` column; the rows below
  it (``Due``, ``Category``, ``Assignment ID``, ``Totals``) give each
  assignment's values in the same columns.
* Student block: a header row starting ``Fullname``, then one row per student.
  In a scores export the header names only the identity columns; the score
  columns line up with the assignment block.
"""

import csv
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# UTC offsets (hours) for the US time zone abbreviations WebAssign prints.
TZ_OFFSETS = {
    "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7, "UTC": 0, "GMT": 0,
}
STUDENT_HEADER = "Fullname"
ASSIGNMENT_HEADER = "Assignment Name"
TOTAL_COLUMN = "Total"


def _is_blank(row: list[str]) -> bool:
    return all(not cell.strip() for cell in row)


def read_blocks(path: Path) -> list[list[list[str]]]:
    """Split a WebAssign export into blocks of rows separated by blank rows."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    blocks, current = [], []
    for row in rows:
        if _is_blank(row):
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(row)
    if current:
        blocks.append(current)
    return blocks


def parse_timestamp(text: str, fmt: str) -> str | None:
    """Parse e.g. ``"Oct 9 2026 11:00 AM EDT"`` with ``fmt`` (sans zone) to ISO 8601."""
    text = " ".join(text.split())
    stamp, _, zone = text.rpartition(" ")
    try:
        when = datetime.strptime(stamp, fmt)
    except ValueError:
        return None
    if zone in TZ_OFFSETS:
        when = when.replace(tzinfo=timezone(timedelta(hours=TZ_OFFSETS[zone])))
    return when.isoformat()


def parse_course_block(block: list[list[str]]) -> dict:
    """Course title, instructor and download time from the first block."""
    lines = [row[0].strip() for row in block]
    title = lines[0] if lines else ""
    course = {"title": title}
    # "MATH-UA 122, section 016+021, Fall 2026"
    match = re.fullmatch(r"(.+?), section (.+), (.+)", title)
    if match:
        course["course"], course["section"], course["term"] = match.groups()
    if len(lines) > 1:
        course["instructor"] = lines[1]
    if len(lines) > 2:
        course["downloaded"] = parse_timestamp(lines[2], "%A, %B %d, %Y %I:%M %p")
        course["downloaded_raw"] = lines[2]
    return course


def _student_block(blocks: list[list[list[str]]], path: Path) -> list[list[str]]:
    for block in blocks:
        if block[0] and block[0][0].strip() == STUDENT_HEADER:
            return block
    raise ValueError(f"No '{STUDENT_HEADER}' header row found in {path}")


def _header(row: list[str]) -> list[str]:
    """Header cells, without the trailing empty column WebAssign adds."""
    cells = [cell.strip() for cell in row]
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _snake(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def _value(cell):
    """A cell as JSON: number if numeric, ``None`` if blank, else the text."""
    if cell is None:
        return None
    text = str(cell).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() and "." not in text else number


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _value(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def _write_json(path: Path, data: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


def _write_csv(path: Path, df: pd.DataFrame) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


class WebAssignRoster:
    """A WebAssign roster export: course details and one row per student."""

    course: dict
    students: pd.DataFrame

    @classmethod
    def from_csv(cls, path: Path) -> "WebAssignRoster":
        path = Path(path)
        blocks = read_blocks(path)
        obj = cls()
        obj.course = parse_course_block(blocks[0])
        block = _student_block(blocks, path)
        columns = _header(block[0])
        rows = [(row + [""] * len(columns))[: len(columns)] for row in block[1:]]
        obj.students = pd.DataFrame(rows, columns=columns, dtype=str)
        return obj

    def to_csv(self, path: Path) -> Path:
        """Write just the student rows, as a plain CSV."""
        return _write_csv(path, self.students)

    def to_json(self, path: Path) -> Path:
        """Write course details and students as one JSON object."""
        return _write_json(path, {"course": self.course, "students": _records(self.students)})


class WebAssignScores:
    """A WebAssign scores export: course details, assignments and student scores."""

    course: dict
    assignments: list[dict]
    students: pd.DataFrame
    identity_columns: list[str]

    @classmethod
    def from_csv(cls, path: Path) -> "WebAssignScores":
        path = Path(path)
        blocks = read_blocks(path)
        obj = cls()
        obj.course = parse_course_block(blocks[0])

        meta = next(
            (b for b in blocks if b[0] and b[0][0].strip() == ASSIGNMENT_HEADER), None
        )
        if meta is None:
            raise ValueError(f"No '{ASSIGNMENT_HEADER}' row found in {path}")
        names = meta[0]
        try:
            total_col = [c.strip() for c in names].index(TOTAL_COLUMN)
        except ValueError:
            raise ValueError(f"No '{TOTAL_COLUMN}' column in the assignment block of {path}") from None
        first_col = total_col + 1
        last_col = max(i for i, c in enumerate(names) if c.strip()) + 1

        obj.assignments = [{"name": html.unescape(names[i].strip())} for i in range(first_col, last_col)]
        for row in meta[1:]:
            key = _snake(row[0])
            if key == "totals":
                obj.course["total_points"] = _value(row[total_col]) if total_col < len(row) else None
                key = "points"
            for j, assignment in enumerate(obj.assignments):
                cell = row[first_col + j] if first_col + j < len(row) else ""
                if key == "due":
                    assignment[key] = parse_timestamp(cell, "%b %d %Y %I:%M %p") or _value(cell)
                else:
                    assignment[key] = _value(cell)

        block = _student_block(blocks, path)
        obj.identity_columns = _header(block[0])[:total_col]
        columns = obj.identity_columns + [TOTAL_COLUMN] + obj._score_columns()
        rows = [(row + [""] * len(columns))[: len(columns)] for row in block[1:]]
        obj.students = pd.DataFrame(rows, columns=columns, dtype=str)
        return obj

    def _score_columns(self) -> list[str]:
        """Assignment names as column headers, made unique with the assignment ID."""
        names = [a["name"] for a in self.assignments]
        return [
            f"{a['name']} [{a.get('assignment_id')}]" if names.count(a["name"]) > 1 else a["name"]
            for a in self.assignments
        ]

    def to_csv(self, path: Path) -> Path:
        """Write the student block, with assignment names as the score column headers."""
        return _write_csv(path, self.students)

    def to_json(self, path: Path) -> Path:
        """Write course, assignments and students (scores keyed by assignment ID) as JSON."""
        score_columns = self._score_columns()
        students = []
        for record in self.students.to_dict(orient="records"):
            student = {k: _value(record[k]) for k in self.identity_columns}
            student["total"] = _value(record[TOTAL_COLUMN])
            student["scores"] = {
                str(a.get("assignment_id") or a["name"]): _value(record[col])
                for a, col in zip(self.assignments, score_columns, strict=True)
            }
            students.append(student)
        return _write_json(
            path, {"course": self.course, "assignments": self.assignments, "students": students}
        )
