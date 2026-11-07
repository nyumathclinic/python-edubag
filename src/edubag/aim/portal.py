"""Page knowledge for the AIM instructor portal's Testing pages (no browser needed).

AIM ("Accessible Information Management", accessiblelearning.com) is the system NYU's Moses Center
uses for testing accommodations. Its instructor portal lists exams students took at the testing
center; ``Testing.aspx?Step=Completed`` lists the ones whose scans have been uploaded, in an
ASP.NET GridView with one row per student exam::

    [checkbox] Download | CRN | Subject | Course | Section | Full Name | Type | Exam Date |
    Time Range | Late | Proctor | Uploaded On

Checking up to 10 rows and clicking "Download Selected Exams" returns a flat ZIP
(``CompletedExam-<n>.zip``) of PDFs named like ``(11057CN) E12740317416842.pdf``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from bs4 import BeautifulSoup

# ASP.NET control-ID prefix of everything in the page's main content
CONTROL_PREFIX = "ctl00_ctl00_ctl00_MainContent_MainContent_MainContent_"
GRID_ID = CONTROL_PREFIX + "GVC"
PAGE_SIZE_ID = CONTROL_PREFIX + "PgGVC_DDLShowLimit"
DOWNLOAD_BUTTON_ID = CONTROL_PREFIX + "PnlDownload_BtnDownload"
FERPA_CONTINUE_ID = CONTROL_PREFIX + "IMConfirmation_BtnContinue"
TERM_NAV_ID = CONTROL_PREFIX + "TermNav"
MAX_PER_DOWNLOAD = 10

# Header text -> ExamRow field
COLUMNS = {
    "CRN": "crn",
    "Subject": "subject",
    "Course": "course",
    "Section": "section",
    "Full Name": "name",
    "Type": "type",
    "Exam Date": "exam_date",
    "Time Range": "time_range",
    "Late": "late",
    "Proctor": "proctor",
    "Uploaded On": "uploaded_on",
}


def _text(el) -> str:
    return " ".join(el.get_text(" ").split()) if el is not None else ""


def parse_date(value: str | dt.date | None) -> dt.date | None:
    """A date from AIM's ``MM/DD/YYYY`` (or ISO ``YYYY-MM-DD``); None if blank."""
    if value is None or isinstance(value, dt.date):
        return value
    value = value.strip()
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date {value!r} (expected MM/DD/YYYY or YYYY-MM-DD)")


@dataclass
class ExamRow:
    """One student's exam in the Completed table."""

    crn: str
    subject: str
    course: str
    section: str
    name: str
    email: str
    type: str
    exam_date: dt.date | None
    time_range: str
    late: str
    proctor: str
    uploaded_on: dt.date | None
    checkbox_id: str

    @property
    def key(self) -> str:
        """Identifies the exam across pages, sorts and sessions (checkbox IDs don't)."""
        when = self.exam_date.isoformat() if self.exam_date else ""
        return "|".join([self.crn, self.email.lower(), self.type, when, self.time_range])

    def to_json(self) -> dict:
        d = asdict(self)
        for k in ("exam_date", "uploaded_on"):
            d[k] = d[k].isoformat() if d[k] else None
        d["key"] = self.key
        return d


def parse_completed_table(html: str) -> list[ExamRow]:
    """The exam rows of a ``Testing.aspx?Step=Completed`` page."""
    soup = BeautifulSoup(html, "lxml")
    grid = soup.find(id=GRID_ID)
    if grid is None:
        if "There is no" in soup.get_text() or "No record" in soup.get_text():
            return []
        raise ValueError("Completed exams table not found on the page")
    rows = grid.find_all("tr")
    header = next((r for r in rows if "gv_header" in (r.get("class") or [])), None)
    if header is None:
        raise ValueError("Completed exams table has no header row")
    names = [_text(th) for th in header.find_all("th")]
    index = {COLUMNS[n]: i for i, n in enumerate(names) if n in COLUMNS}
    missing = set(COLUMNS.values()) - set(index)
    if missing:
        raise ValueError(f"Completed exams table is missing columns {sorted(missing)}; got {names}")

    out = []
    for r in rows:
        if r is header:
            continue
        cells = r.find_all("td", recursive=False)
        checkbox = r.find("input", type="checkbox")
        if len(cells) < len(names) or checkbox is None:
            continue  # pager/empty-data rows
        values = {f: _text(cells[i]) for f, i in index.items()}
        mailto = cells[index["name"]].find("a", href=re.compile(r"^mailto:", re.I))
        email = mailto["href"].split(":", 1)[1].strip() if mailto else ""
        out.append(
            ExamRow(
                crn=values["crn"],
                subject=values["subject"],
                course=values["course"],
                section=values["section"],
                name=values["name"],
                email=email,
                type=values["type"],
                exam_date=parse_date(values["exam_date"]),
                time_range=values["time_range"],
                late=values["late"],
                proctor=values["proctor"],
                uploaded_on=parse_date(values["uploaded_on"]),
                checkbox_id=checkbox.get("id", ""),
            )
        )
    return out


def parse_record_count(html: str) -> int | None:
    """The "Records Found: N" total, if shown."""
    m = re.search(r"Records Found:\s*(?:<[^>]+>\s*)*(\d+)", html)
    return int(m.group(1)) if m else None


def parse_term(html: str) -> str | None:
    """The term the page shows, e.g. "Fall 2026"."""
    soup = BeautifulSoup(html, "lxml")
    nav = soup.find(id=TERM_NAV_ID)
    m = re.search(r"Term:\s*(.+)", _text(nav.find("strong")) if nav and nav.find("strong") else "")
    return m.group(1).strip() if m else None


def _as_set(value: str | Iterable[str] | None) -> frozenset[str] | None:
    if value is None:
        return None
    items = [value] if isinstance(value, str) else list(value)
    return frozenset(str(v).strip().casefold() for v in items) or None


@dataclass(frozen=True)
class ExamFilter:
    """Which Completed rows to take. Unset criteria match everything; sets match any member.

    ``date`` alone matches that exam date; with ``until``, the inclusive range ``date..until``.
    """

    crn: frozenset[str] | None = None
    type: frozenset[str] | None = None
    subject: frozenset[str] | None = None
    course: frozenset[str] | None = None
    section: frozenset[str] | None = None
    date: dt.date | None = None
    until: dt.date | None = None

    @classmethod
    def build(
        cls,
        *,
        crn=None,
        type=None,  # noqa: A002 - matches the column name
        subject=None,
        course=None,
        section=None,
        date: str | dt.date | None = None,
        until: str | dt.date | None = None,
        grace_days: int | None = None,
    ) -> ExamFilter:
        """Build a filter from loose values (strings or lists; ISO or MM/DD/YYYY dates).

        ``grace_days`` is an alternative to ``until``: ``until = date + grace_days``.
        """
        start = parse_date(date)
        end = parse_date(until)
        if grace_days is not None:
            if end is not None:
                raise ValueError("Give either until or grace_days, not both")
            if start is None:
                raise ValueError("grace_days needs a date")
            end = start + dt.timedelta(days=int(grace_days))
        if end is not None and start is None:
            raise ValueError("until needs a date")
        if start and end and end < start:
            raise ValueError(f"until ({end}) is before date ({start})")
        return cls(
            crn=_as_set(crn),
            type=_as_set(type),
            subject=_as_set(subject),
            course=_as_set(course),
            section=_as_set(section),
            date=start,
            until=end,
        )

    @property
    def end(self) -> dt.date | None:
        """Last matching exam date."""
        return self.until or self.date

    def matches(self, row: ExamRow) -> bool:
        for attr in ("crn", "type", "subject", "course", "section"):
            wanted = getattr(self, attr)
            if wanted is not None and getattr(row, attr).strip().casefold() not in wanted:
                return False
        if self.date is not None:
            if row.exam_date is None or not (self.date <= row.exam_date <= self.end):
                return False
        return True

    def describe(self) -> str:
        parts = []
        for attr in ("crn", "type", "subject", "course", "section"):
            v = getattr(self, attr)
            if v:
                parts.append(f"{attr} in {sorted(v)}")
        if self.date:
            parts.append(f"date {self.date}" + (f"..{self.until}" if self.until else ""))
        return ", ".join(parts) or "all rows"


def chunks(items: list, size: int = MAX_PER_DOWNLOAD) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def safe_member_name(name: str) -> str | None:
    """A ZIP member's bare file name, or None for directories and unsafe/empty names."""
    if name.endswith(("/", "\\")):
        return None
    base = PurePosixPath(PureWindowsPath(name).as_posix()).name
    if base in ("", ".", "..") or base.startswith("."):
        return None
    return base


def extract_zip(zip_path: Path, dest: Path) -> list[Path]:
    """Extract a ZIP's files flat into ``dest``, safely.

    Only bare file names are used (no directories, ``..`` or absolute paths). A file already in
    ``dest`` with the same name and content is reused; with different content the new one gets a
    ``-1``, ``-2``, ... suffix. Returns the paths of the extracted (or matching) files.
    """
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            base = safe_member_name(info.filename)
            if base is None:
                continue
            tmp = dest / f".{base}.part"
            with zf.open(info) as src, tmp.open("wb") as dst:
                while block := src.read(1 << 20):
                    dst.write(block)
            digest = _sha256(tmp)
            stem, suffix = Path(base).stem, Path(base).suffix
            target, n = dest / base, 0
            while target.exists() and _sha256(target) != digest:
                n += 1
                target = dest / f"{stem}-{n}{suffix}"
            if target.exists():
                tmp.unlink()
            else:
                tmp.rename(target)
            out.append(target)
    return out
