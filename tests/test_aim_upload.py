"""Tests for uploading personalized exams to AIM: parsing, planning, ledger, session logic."""

import datetime as dt
import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from edubag.aim import app, portal, upload
from edubag.aim.client import AimClient, is_change_request
from edubag.aim.ledger import Ledger

FIXTURE = Path(__file__).parent / "fixtures" / "aim" / "upcoming.html"
NETIDS = ["ss1234", "pp2345", "ce3456", "ds4567", "et5678", "fm6789", "gd7890", "zz9999"]


@pytest.fixture
def rows():
    return portal.parse_upcoming_table(FIXTURE.read_text())


@pytest.fixture
def config(tmp_path):
    lines = [
        "module: q09",
        "tag: q09-v0.2",
        "exam: Quiz 9",
        "match: {type: Quiz, subject: MATH-UA, course: '120', section: '016', date: 2026-11-20, before: 2026-12-04}",
        "ledger: ledger.json",
        "students:",
    ]
    for n in NETIDS:
        (tmp_path / f"q09-{n}.qns.pdf").write_bytes(b"%PDF-1.5\n%fake\n")
        lines.append(f"  - {{netid: {n}, name: Student {n.upper()}, pdf: q09-{n}.qns.pdf}}")
    path = tmp_path / "q09-aim.yml"
    path.write_text("\n".join(lines) + "\n")
    return upload.load_upload_config(path)


def test_parse_upcoming(rows):
    assert len(rows) == 9
    first = rows[0]
    assert (first.crn, first.subject, first.course, first.section) == ("10433", "MATH-UA", "120", "016")
    assert first.netid == "ss1234"
    assert first.type == "Quiz"
    assert first.exam_date == dt.date(2026, 11, 20)
    assert first.status == "Approved"
    assert first.files == []
    assert first.checkbox_id == portal.CONTROL_PREFIX + "GV_ctl02_ChkBox"
    pat = rows[1]
    assert [f.title for f in pat.files] == ["Quiz 9 · q09-v0.1 · Pat Person (pp2345)"]
    assert pat.files[0].uploaded_on == dt.date(2026, 11, 17)
    assert "Plan=Delete" in pat.files[0].delete_href
    assert rows[4].status == "Pending"
    assert len({r.key for r in rows}) == len(rows)


def test_netid_needs_nyu_address():
    row = portal.UpcomingRow("1", "S", "C", "1", "N", "x1@example.com", "Quiz", None, "", "Approved", "", [])
    assert row.netid == ""


def test_config(config, tmp_path):
    assert config.filter.date == dt.date(2026, 11, 20)
    assert config.filter.until == dt.date(2026, 12, 3)
    assert config.ledger == tmp_path / "ledger.json"
    s = config.students[0]
    assert config.title_for(s) == "Quiz 9 · q09-v0.2 · Student SS1234 (ss1234)"


def test_title_is_cut_to_aim_limit(config):
    long = upload.Student("ab1", "X" * 200, Path("x.pdf"))
    title = config.title_for(long)
    assert len(title) <= portal.UPLOAD_TITLE_MAX
    assert "q09-v0.2" in title


def test_config_rejects_foreign_tag(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text("module: q09\ntag: q10-v0.1\nmatch: {type: Quiz, date: 2026-11-20}\n")
    with pytest.raises(ValueError, match="not a q09-v"):
        upload.load_upload_config(p)


def test_file_tag(config):
    tag = config.file_tag
    assert tag(portal.ExamFile("Quiz 9 · q09-v0.1 · X", None, "")) == "q09-v0.1"
    assert tag(portal.ExamFile("Exam File Without No Title", None, "")) is None
    assert tag(portal.ExamFile("Quiz 10 · xq09-v0.1", None, "")) is None


def test_plan(config, rows):
    kinds = {a.netid: a.kind for a in upload.plan(config, rows)}
    assert kinds == {
        "ss1234": upload.UPLOAD,  # 12/04 session is outside the window (next quiz)
        "pp2345": upload.REPLACE,
        "ce3456": upload.CLEANUP,  # makeup on 11/23 has current + older file
        "ds4567": upload.MANUAL,
        "et5678": upload.WAITING,
        "fm6789": upload.AMBIGUOUS,
        "gd7890": upload.WAITING,  # an Exam, not a Quiz
        "zz9999": upload.WAITING,
    }


def test_plan_details(config, rows):
    by = {a.netid: a for a in upload.plan(config, rows)}
    row = by["ss1234"].row
    assert row is not None and row.exam_date == dt.date(2026, 11, 20)
    assert by["pp2345"].stale_tags == ["q09-v0.1"]
    assert "Pending" in by["et5678"].detail
    for a in by.values():  # log lines never carry names or emails
        line = a.describe()
        assert "@" not in line and "Student" not in line and "Person" not in line


def test_plan_after_new_tag_replaces(config, rows):
    config.tag = "q09-v0.3"
    by = {a.netid: a for a in upload.plan(config, rows)}
    assert by["ce3456"].kind == upload.REPLACE
    assert sorted(by["ce3456"].stale_tags) == ["q09-v0.1", "q09-v0.2"]


def test_plan_only_netids_and_missing_pdf(config, rows):
    config.students[0].pdf.unlink()
    actions = upload.plan(config, rows, {"ss1234"})
    assert [(a.netid, a.kind) for a in actions] == [("ss1234", upload.NO_PDF)]


def test_check_pdf(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"hello")
    with pytest.raises(ValueError, match="Not a PDF"):
        upload.check_pdf(p)
    with pytest.raises(FileNotFoundError):
        upload.check_pdf(tmp_path / "missing.pdf")


def test_ledger(tmp_path):
    path = tmp_path / "Students" / "aim-uploads.json"
    led = Ledger(path)
    led.record("q09", "ab1234", tag="q09-v0.1", sha256="x", session="k")
    led.record("q09", "ab1234", tag="q09-v0.2", sha256="y", session="k", replaced=["q09-v0.1"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    again = Ledger(path)
    assert [e["tag"] for e in again.entries("q09", "ab1234")] == ["q09-v0.1", "q09-v0.2"]
    assert (again.last("q09", "ab1234") or {})["replaced"] == ["q09-v0.1"]
    assert again.last("q09", "cd5678") is None
    assert not list(path.parent.glob(".*"))  # no temp files left


def test_ledger_rejects_unknown_version(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"version": 99, "uploads": {}}))
    with pytest.raises(ValueError):
        Ledger(p)


@pytest.mark.parametrize(
    ("url", "body", "blocked"),
    [
        ("https://x/NYU/instructor/Testing.aspx?Step=Exams&Plan=Delete&ID=1", None, True),
        ("https://x/NYU/instructor/Testing.aspx?Step=Exams", b"__EVENTTARGET=ctl00%24GFormUpload%24BtnUpload", True),
        ("https://x/NYU/instructor/Testing.aspx?Step=Exams", b"__EVENTTARGET=ctl00%24PgGV%24DDLShowLimit", False),
        ("https://x/NYU/instructor/Testing.aspx?Step=Exams", None, False),
    ],
)
def test_dry_run_guard(url, body, blocked):
    assert is_change_request(url, body) is blocked


def _fake_session(html_states, calls):
    """A _session stand-in: runs ``work`` against fixture HTML, without a browser."""

    def session(work, headless, username, password, term, open_page=None, read_only=False):
        calls.append(("session", read_only))
        return work(object(), html_states[0])

    return session


def test_upload_exams_dry_run(monkeypatch, config):
    client = AimClient(auth_state_path=Path("/nonexistent"))
    calls = []
    monkeypatch.setattr(client, "_session", _fake_session([FIXTURE.read_text()], calls))
    monkeypatch.setattr(client, "_upload_file", lambda *a: pytest.fail("dry run uploaded"))
    actions = client.upload_exams(config, dry_run=True)
    assert calls == [("session", True)]
    assert {a.netid: a.kind for a in actions}["ss1234"] == upload.UPLOAD
    assert not config.ledger.exists()


def test_upload_exams_records_and_survives_retry(monkeypatch, config):
    client = AimClient(auth_state_path=Path("/nonexistent"))
    html = FIXTURE.read_text()
    uploads, deletes = [], []
    attempts = {"n": 0}

    def session(work, headless, username, password, term, open_page=None, read_only=False):
        assert not read_only
        try:
            return work(object(), html)
        except RuntimeError:  # what _run_with_reauth does after an expired login
            return work(object(), html)

    def fake_upload(page, cfg, a):
        uploads.append(a.netid)
        attempts["n"] += 1
        if attempts["n"] == 2:
            raise RuntimeError("login expired")

    monkeypatch.setattr(client, "_session", session)
    monkeypatch.setattr(client, "_upload_file", fake_upload)
    monkeypatch.setattr(client, "_delete_files", lambda page, a: deletes.append(a.netid))
    actions = client.upload_exams(config)
    # ss1234 uploaded once; pp2345 failed mid-upload, then retried; nobody uploaded twice after success
    assert uploads == ["ss1234", "pp2345", "pp2345"]
    assert sorted(deletes) == ["ce3456", "pp2345"]
    led = Ledger(config.ledger)
    assert (led.last("q09", "ss1234") or {})["tag"] == "q09-v0.2"
    assert (led.last("q09", "pp2345") or {})["replaced"] == ["q09-v0.1"]
    assert len(led.entries("q09", "ss1234")) == 1
    assert {a.netid: a.kind for a in actions}["ds4567"] == upload.MANUAL


def test_cli_help():
    result = CliRunner().invoke(app, ["client", "upload-exams", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output
