"""Planning uploads of personalized exams to AIM's Upcoming Exams sessions (no browser needed).

An upload config (YAML; JSON is fine too), usually written by a build tool::

    module: q09                 # what the files are copies of
    tag: q09-v0.2               # their version; a new tag replaces files of older ones
    exam: Quiz 9                # for the file title
    match:                      # which sessions are this exam's
      type: Quiz
      subject: MATH-UA
      course: "120"
      section: "016"
      date: 2026-11-20          # first session date (the exam's date)
      before: 2026-12-04        # optional: sessions before this date (the next exam's) only
    ledger: ../../Students/aim-uploads.json   # optional; relative to this file
    title: "{exam} · {tag} · {name} ({netid})" # optional; cut to AIM's 100 characters
    students:
      - {netid: ab1234, name: Alex Blue, pdf: personalized/q09-ab1234.qns.pdf}

Each file's title carries the tag, so AIM's own file list tells which version a session has:
a session gets a file if it has none of this module's, or only ones with an older tag (those are
deleted after the new one is uploaded). Sessions holding files edubag didn't upload are left
alone.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from edubag.aim.portal import UPLOAD_MAX_BYTES, UPLOAD_TITLE_MAX, ExamFile, ExamFilter, UpcomingRow, parse_date

DEFAULT_TITLE = "{exam} · {tag} · {name} ({netid})"
NETID = re.compile(r"^[a-z]+\d+$")


def check_pdf(path: Path) -> Path:
    """An existing PDF no bigger than AIM allows (else ValueError/FileNotFoundError)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")
    with path.open("rb") as f:
        if f.read(4) != b"%PDF":
            raise ValueError(f"Not a PDF: {path}")
    if path.stat().st_size > UPLOAD_MAX_BYTES:
        raise ValueError(f"{path.name} is over AIM's {UPLOAD_MAX_BYTES // 1_000_000} MB limit")
    return path


@dataclass
class Student:
    netid: str
    name: str
    pdf: Path


@dataclass
class UploadConfig:
    module: str
    tag: str
    exam: str
    filter: ExamFilter
    students: list[Student]
    ledger: Path | None = None
    title: str = DEFAULT_TITLE
    statuses: frozenset[str] = frozenset({"approved"})

    def title_for(self, student: Student) -> str:
        title = self.title.format(exam=self.exam, tag=self.tag, name=student.name, netid=student.netid)
        return title[:UPLOAD_TITLE_MAX].rstrip()

    def file_tag(self, f: ExamFile) -> str | None:
        """The tag of this module in a file's title, or None if the file isn't one of ours."""
        m = re.search(rf"(?<![\w-]){re.escape(self.module)}-v[0-9][\w.]*", f.title)
        return m.group(0) if m else None


def load_upload_config(path: Path) -> UploadConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    base = path.parent

    def need(key):
        if not data.get(key):
            raise ValueError(f"{path}: missing {key!r}")
        return str(data[key])

    module, tag = need("module"), need("tag")
    if not tag.startswith(module + "-v"):
        raise ValueError(f"{path}: tag {tag!r} is not a {module}-v... tag")
    match = dict(data.get("match") or {})
    if not match.get("type") or not match.get("date"):
        raise ValueError(f"{path}: match needs at least type and date")
    before = parse_date(str(match.pop("before"))) if match.get("before") else None
    flt = ExamFilter.build(
        crn=match.get("crn"),
        type=match.get("type"),
        subject=match.get("subject"),
        course=match.get("course"),
        section=match.get("section"),
        date=str(match["date"]),
        until=before - dt.timedelta(days=1) if before else dt.date.max,
    )
    students, seen = [], set()
    for s in data.get("students") or []:
        netid = str(s.get("netid", "")).strip().lower()
        if not NETID.match(netid):
            raise ValueError(f"{path}: bad NetID {netid!r}")
        if netid in seen:
            raise ValueError(f"{path}: {netid} is listed twice")
        seen.add(netid)
        students.append(Student(netid=netid, name=str(s.get("name", "")).strip(), pdf=base / str(s["pdf"])))
    ledger = data.get("ledger")
    statuses = data.get("statuses") or ["Approved"]
    return UploadConfig(
        module=module,
        tag=tag,
        exam=str(data.get("exam") or module),
        filter=flt,
        students=students,
        ledger=(base / str(ledger)) if ledger else None,
        title=str(data.get("title") or DEFAULT_TITLE),
        statuses=frozenset(str(x).casefold() for x in statuses),
    )


# What to do for one student
UPLOAD = "upload"  # session has no file of this module: upload
REPLACE = "replace"  # session has only older-tag files: upload, then delete those
CLEANUP = "cleanup"  # session has the current file and older ones: delete the older ones
CURRENT = "current"  # session already has the current file
WAITING = "waiting"  # no (approved) session yet
AMBIGUOUS = "ambiguous"  # several approved sessions match
MANUAL = "manual"  # session has a file edubag didn't upload; left alone
NO_PDF = "no-pdf"  # would upload, but the PDF is missing

ACTS = {UPLOAD, REPLACE, CLEANUP}


@dataclass
class Action:
    student: Student
    kind: str
    row: UpcomingRow | None = None
    stale: list[ExamFile] = field(default_factory=list)
    detail: str = ""
    stale_tags: list[str] = field(default_factory=list)

    @property
    def netid(self) -> str:
        return self.student.netid

    def describe(self) -> str:
        """One line, with no student name or email (safe for logs)."""
        where = ""
        if self.row is not None:
            where = f"{self.row.type} {self.row.exam_date} {self.row.time_range}"
        parts = [f"{self.netid:<10} {self.kind:<9}", where]
        if self.stale:
            parts.append("replacing " + ", ".join(sorted({t for t in self.stale_tags if t})))
        if self.detail:
            parts.append(self.detail)
        return "  ".join(p for p in parts if p).rstrip()


def plan(config: UploadConfig, rows: list[UpcomingRow], netids: set[str] | None = None) -> list[Action]:
    """What to do for each student in ``config`` (or only those in ``netids``), given AIM's rows."""
    by_netid: dict[str, list[UpcomingRow]] = {}
    for r in rows:
        if r.netid and config.filter.matches(r):
            by_netid.setdefault(r.netid, []).append(r)

    actions = []
    for s in config.students:
        if netids is not None and s.netid not in netids:
            continue
        sessions = by_netid.get(s.netid, [])
        approved = [r for r in sessions if r.status.casefold() in config.statuses]
        if not approved:
            others = sorted({r.status for r in sessions})
            detail = f"session status {', '.join(others)}" if others else "no session yet"
            actions.append(Action(s, WAITING, detail=detail))
            continue
        if len(approved) > 1:
            when = "; ".join(f"{r.exam_date} {r.time_range}" for r in approved)
            actions.append(Action(s, AMBIGUOUS, detail=f"{len(approved)} sessions: {when}"))
            continue
        row = approved[0]
        tagged = [(f, config.file_tag(f)) for f in row.files]
        current = [f for f, t in tagged if t == config.tag]
        stale = [f for f, t in tagged if t and t != config.tag]
        foreign = [f for f, t in tagged if not t]
        stale_tags = [config.file_tag(f) or "" for f in stale]
        if current:
            kind = CLEANUP if stale else CURRENT
            actions.append(Action(s, kind, row, stale, stale_tags=stale_tags))
        elif foreign and not stale:
            actions.append(Action(s, MANUAL, row, detail=f"{len(foreign)} file(s) not uploaded by edubag"))
        elif not s.pdf.is_file():
            actions.append(Action(s, NO_PDF, row, detail=f"{s.pdf.name} not found"))
        else:
            actions.append(Action(s, REPLACE if stale else UPLOAD, row, stale, stale_tags=stale_tags))
    return actions
