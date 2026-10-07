import io
import json
import re
import shutil
import sys

from bs4 import BeautifulSoup
from loguru import logger
import pandas as pd
from pathlib import Path

from edubag.albert.details import clean_value, parse_detail_field
from edubag.albert.term import Term

# Albert's class roster page puts each student in a "win0divBIGGRP$<row>"
# box. Inside it, field element ids look like "<FIELD>$<row>" or
# "<FIELD>$<n>$$<row>"; map the field names to student columns.
HTML_STUDENT_FIELDS = {
    "SCC_PRFPRIMNMVW_NAME_DISPLAY": "name",
    "NYU_NMCOACH_WRK_NYU_PRONOUN": "pronoun",
    "NYU_GRD_RST_VW_CAMPUS_ID": "campus_id",
    "SCC_PREF_EMAIL_EMAIL_ADDR": "email",
    "SCC_PREF_PHN_VW_PHONE": "phone",
    "DROP_REASON1": "drop_reason",
    "ACAD_CAR_TBL_DESCR": "career",
    "PROGPLAN2": "progplan",
    "CLASS_ROSTER_VW_ACADEMIC_LEVEL": "level",
    "NYU_NMCOACH_WRK_DESCR50": "study_away",
}
HTML_PHOTO_FIELD = "NYU_EMP_SPIC_VW_EMPLOYEE_PHOTO"
HTML_STUDENT_BOX = re.compile(r"^win0divBIGGRP\$(\d+)$")
HTML_FIELD_ID = re.compile(r"^([A-Z][A-Z0-9_]*)(?:\$\d+\$)?\$(\d+)$")
# A course code such as "MATH-UA 122" anywhere in the course details.
COURSE_CODE = re.compile(r"\b([A-Z]{2,}-[A-Z]{2,})\s+(\d+[A-Z]*)\b")

STUDENT_COLUMNS = [
    "campus_id", "name", "first", "last", "pronoun", "email", "netid",
    "career", "program", "plan", "level", "study_away", "drop_reason",
    "phone", "photo",
]


def unpack_progplan(progplan: str) -> tuple[str, str]:
    """Split Albert's "program - plan" string into (program, plan).

    >>> unpack_progplan("UA-Coll of Arts & Sci - \\n\\nUndecided")
    ('UA-Coll of Arts & Sci', 'Undecided')
    """
    parts = re.split(r"\s+-\s+", progplan.strip(), maxsplit=1)
    program = parts[0].strip()
    plan = parts[1].strip() if len(parts) > 1 else ""
    return program, plan


def parse_course_details(soup: BeautifulSoup) -> dict:
    """Course details from a roster page: labeled fields outside student boxes.

    Uses the same keys as :meth:`AlbertClient.fetch_course_details`
    (``course_name``, ``class_number``, ``section``, ``term``, ...) plus the
    keys :meth:`AlbertRoster.from_xls` produces (``Semester``, ``Section``,
    ...), so :attr:`AlbertRoster.pathstem` works.
    """
    course: dict = {}
    for el in soup.select(".psc_has_value"):
        if el.find_parent(id=HTML_STUDENT_BOX) is not None:
            continue
        label = el.select_one(".ps-label")
        value = el.select_one(".ps_box-value")
        if label is None or value is None:
            continue
        parent_id = label.parent.get("id") if label.parent else None
        course.update(parse_detail_field(label.get_text(), parent_id, value.get_text(" ")))
    if not course:
        logger.warning("No course details found on the roster page (no labeled fields outside student boxes)")
    else:
        logger.debug(f"Course detail keys found on roster page: {sorted(course)}")
    return add_roster_keys(course)


def add_roster_keys(course: dict) -> dict:
    """Add the keys from_xls produces (``Semester``, ``Section``, ...) to class details.

    Lets :attr:`AlbertRoster.pathstem` and existing tools work with details
    scraped from a roster page.
    """
    if "term" in course:
        course["Semester"] = str(course["term"])
    if "section" in course:
        course["Section"] = str(course["section"])
    if "class_number" in course:
        course["Class Number"] = str(course["class_number"])
    for value in course.values():
        match = COURSE_CODE.search(str(value))
        if match:
            course["Subject Code"], course["Catalog Number"] = match.groups()
            break
    return course


class AlbertRoster(object):
    """A class roster fetched from Albert"""

    course: dict[str, str]
    students: pd.DataFrame
    # Directory that relative ``photo`` paths in ``students`` resolve against.
    base_dir: Path | None = None

    @classmethod
    def from_xls(cls, path: Path):
        """
        Parses an HTML file to extract a class roster table into a pandas
        DataFrame and class metadata into a dictionary.

        Args:
            path (Path): The path to the HTML file.

        Returns:
            AlbertRoster: a roster
        """
        # Debated about whether this should be a module function
        # or a class method. Opted for the latter after reading
        # https://softwareengineering.stackexchange.com/a/166715/149470
        # Dictionary to store the extracted data
        parsed_data = {"metadata": {}, "dataframe": None}

        # Read the HTML content from the file
        with open(path, "r", encoding="utf-8") as f:
            html_content = f.read()

        # Parse the HTML with BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")

        # --- Extract Metadata ---
        # The metadata is in custom tags with 'b' elements inside them.
        # Find all 'b' tags to get the key-value pairs.
        for tag in soup.find_all("b"):
            # The key is the text of the 'b' tag, stripped of the colon
            key = tag.get_text().strip(": ")
            # The value is the text of the parent tag, with the key text removed
            parent = tag.parent
            if parent is None:
                continue
            parent_text = parent.get_text()
            value = parent_text.replace(tag.get_text(), "").strip()
            # Store in the metadata dictionary
            if key and value:
                parsed_data["metadata"][key] = value

        # --- Further parse metadata ---
        # The "Class Detail" field is a string like "MATH-UA 122 (0)-001"
        # The substring "MATH-UA" is the subject code
        # The substring "122" is the catalog number
        # The substring "001" is the section number
        class_detail = parsed_data["metadata"].get("Class Detail", "")
        if class_detail:
            # Parse "MATH-UA 122 (0)-001" format: subject code, catalog number, section
            match = re.match(r"(.+?)\s+(\d+)\s*\(.*?\)-(.+)", class_detail)
            if match:
                parsed_data["metadata"]["Subject Code"] = match.group(1)
                parsed_data["metadata"]["Catalog Number"] = match.group(2)
                parsed_data["metadata"]["Section"] = match.group(3)

        # --- Extract DataFrame ---
        # pandas.read_html can directly parse the table into a DataFrame.
        # It returns a list of DataFrames, so we take the first one.
        tables = pd.read_html(io.StringIO(html_content))
        if tables:
            parsed_data["dataframe"] = tables[0]
            # Drop the "Counter" column if it exists
            if "Counter" in parsed_data["dataframe"].columns:
                parsed_data["dataframe"] = parsed_data["dataframe"].drop(columns=["Counter"])

        obj = AlbertRoster()
        obj.course = parsed_data["metadata"]
        obj.students = parsed_data["dataframe"]
        return obj

    @classmethod
    def from_course(cls, course: dict):
        """A roster with course details and no students (e.g. to get ``pathstem``)."""
        obj = cls()
        obj.course = course
        obj.students = pd.DataFrame(columns=STUDENT_COLUMNS)
        return obj

    @classmethod
    def from_html(cls, path: Path):
        """Parse a saved Albert class roster page (HTML), including photos.

        Each student is a ``win0divBIGGRP$<row>`` box; fields inside it are
        found by element id (see ``HTML_STUDENT_FIELDS``), and the photo is
        the ``<img>`` whose id starts with ``HTML_PHOTO_FIELD``, with ``src``
        resolved relative to the HTML file. Course details come from the
        labeled fields outside the student boxes, using the same keys as
        :meth:`AlbertClient.fetch_course_details`.

        Args:
            path (Path): The saved roster page.

        Returns:
            AlbertRoster: a roster whose ``students`` include a ``photo`` path
            (or None) for each student.
        """
        path = Path(path)
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")

        def text(el) -> str:
            return clean_value(el.get_text(" ", strip=True)) or ""

        rows = []
        for box in soup.find_all(id=HTML_STUDENT_BOX):
            record: dict = {}
            for el in box.find_all(id=HTML_FIELD_ID):
                field = HTML_FIELD_ID.match(el["id"]).group(1)
                if field == HTML_PHOTO_FIELD and el.name == "img" and el.get("src"):
                    # Saved rosters point src at a local copy; an unsaved server
                    # path won't exist locally, and to_json then records no photo.
                    record["photo"] = str(path.parent / el["src"].lstrip("/"))
                elif field in HTML_STUDENT_FIELDS and text(el):
                    record[HTML_STUDENT_FIELDS[field]] = text(el)
            if not record.get("name"):
                continue
            # Albert shows "First Last"; take the last word as the family name.
            first, _, last = record["name"].rpartition(" ")
            program, plan = unpack_progplan(record.get("progplan", ""))
            email = record.get("email", "")
            rows.append(
                {
                    **record,
                    "first": first or last,
                    "last": last if first else "",
                    "netid": email.split("@")[0] if "@" in email else "",
                    "program": program,
                    "plan": plan,
                }
            )

        course = parse_course_details(soup)

        obj = cls()
        obj.course = course
        obj.students = pd.DataFrame(rows).reindex(columns=STUDENT_COLUMNS)
        obj.base_dir = None  # photo paths are absolute (or relative to the cwd)
        return obj

    def photo_path(self, photo) -> Path | None:
        """Resolve a student's ``photo`` value to a file path, if any."""
        if not isinstance(photo, str) or not photo:
            return None
        p = Path(photo)
        return p if p.is_absolute() or self.base_dir is None else self.base_dir / p

    def to_json(self, path: Path) -> Path:
        """Save the roster as JSON, copying photos into ``<stem>_photos/``.

        Photos are renamed ``<netid>.<ext>`` (or ``<campus_id>`` when there is no
        NetID) and stored relative to the JSON file, so the pair can be moved
        together.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        photo_dir = path.parent / f"{path.stem}_photos"
        students = []
        for record in self.students.to_dict(orient="records"):
            record = {k: (None if pd.isna(v) else v) for k, v in record.items()}
            source = self.photo_path(record.get("photo"))
            if source is not None and source.exists():
                photo_dir.mkdir(exist_ok=True)
                target = photo_dir / f"{record.get('netid') or record.get('campus_id')}{source.suffix or '.jpg'}"
                if source.resolve() != target.resolve():
                    shutil.copyfile(source, target)
                record["photo"] = str(target.relative_to(path.parent))
            else:
                record["photo"] = None
            students.append(record)
        path.write_text(json.dumps({"course": self.course, "students": students}, indent=2, ensure_ascii=False))
        return path

    @classmethod
    def from_json(cls, path: Path):
        """Load a roster saved by :meth:`to_json`."""
        path = Path(path)
        data = json.loads(path.read_text())
        obj = cls()
        obj.course = data["course"]
        obj.students = pd.DataFrame(data["students"]).reindex(columns=STUDENT_COLUMNS)
        obj.base_dir = path.parent
        return obj

    @property
    def pathstem(self) -> str:
        """A string serializing the course metatdata for use in file paths."""
        section = self.course.get("Section", "000")
        semester = self.course.get("Semester")
        term = Term.from_name(semester).code if semester else "UNKNOWN"
        if "Subject Code" not in self.course and "Class Number" in self.course:
            # The HTML roster page names the class only by its class number.
            return f"class{self.course['Class Number']}_{section}_{term}"
        subject = self.course.get("Subject Code", "UNKNOWN")
        catalog = self.course.get("Catalog Number", "000")
        return f"{subject}_{catalog}_{section}_{term}"

    def to_csv(self, path_or_buf):
        """Saves the roster DataFrame to CSV format.

        Args:
            path_or_buf (Path | file-like): The file path or buffer to write the CSV data to.
            See `pandas.DataFrame.to_csv`_ for details.

        Warning:
            This method only saves the students DataFrame to CSV format. The course metadata
            is not saved. Use the `pathstem` property to get a string representation of the course
            metadata for use in file paths.

        .. _pandas.DataFrame.to_csv: https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.DataFrame.to_csv
        """
        self.students.to_csv(path_or_buf, index=False)


if __name__ == "__main__":
    # assume the first argument is a path and try to parse it
    import sys

    path = sys.argv[1]
    roster = AlbertRoster.from_xls(Path(path))
    if roster:
        print("--- Course Dictionary ---")
        for key, value in roster.course.items():
            print(f"{key}: {value}")

        print("\n--- Roster DataFrame ---")
        print(roster.students.head())
        print("...")
