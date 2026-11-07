"""Tests for the AIM portal page parsing, row filters and ZIP extraction (no browser)."""

import datetime as dt
import zipfile
from pathlib import Path

import pytest

from edubag.aim import portal
from edubag.aim.portal import ExamFilter
from edubag.nyu_sso import drop_session_cookies

FIXTURE = Path(__file__).parent / "fixtures" / "aim" / "completed.html"


@pytest.fixture
def rows():
    return portal.parse_completed_table(FIXTURE.read_text())


def test_parse_rows(rows):
    assert len(rows) == 4
    first = rows[0]
    assert first.crn == "10493"
    assert (first.subject, first.course, first.section) == ("MATH-UA", "122", "021")
    assert first.name == "Susie Student"
    assert first.email == "ss1234@nyu.edu"
    assert first.type == "Quiz"
    assert first.exam_date == dt.date(2026, 11, 6)
    assert first.time_range == "12:15 PM - 12:55 PM (40)"
    assert first.late == ""
    assert first.proctor == "Not Assigned"
    assert first.uploaded_on == dt.date(2026, 11, 6)
    assert first.checkbox_id == portal.CONTROL_PREFIX + "GVC_ctl02_ChkBox"
    assert rows[3].uploaded_on is None


def test_key_is_stable_and_unique(rows):
    keys = [r.key for r in rows]
    assert len(set(keys)) == len(keys)
    assert keys[1] == "10488|pp4321@nyu.edu|Exam|2026-11-05|02:55 PM - 04:40 PM (105)"


def test_page_info():
    html = FIXTURE.read_text()
    assert portal.parse_record_count(html) == 4
    assert portal.parse_term(html) == "Fall 2026"


def test_missing_column_is_an_error():
    html = FIXTURE.read_text().replace(">Proctor<", ">Assistant<")
    with pytest.raises(ValueError, match="proctor"):
        portal.parse_completed_table(html)


def test_filter_crn_type_and_date_range(rows):
    flt = ExamFilter.build(crn=["10488", "10493"], type="exam", date="2026-11-05", until="2026-11-13")
    assert [r.name for r in rows if flt.matches(r)] == ["Pat Person", "Robin Sample"]


def test_filter_date_alone_matches_only_that_day(rows):
    flt = ExamFilter.build(date="2026-11-05")
    assert [r.name for r in rows if flt.matches(r)] == ["Pat Person", "Alex Example"]


def test_filter_grace_days(rows):
    flt = ExamFilter.build(type="Exam", date=dt.date(2026, 11, 5), grace_days=8)
    assert flt.until == dt.date(2026, 11, 13)
    assert len([r for r in rows if flt.matches(r)]) == 3


def test_filter_accepts_aim_dates():
    assert ExamFilter.build(date="11/05/2026").date == dt.date(2026, 11, 5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"date": "2026-11-05", "until": "2026-11-13", "grace_days": 3},
        {"until": "2026-11-13"},
        {"grace_days": 3},
        {"date": "2026-11-13", "until": "2026-11-05"},
    ],
)
def test_filter_bad_dates(kwargs):
    with pytest.raises(ValueError):
        ExamFilter.build(**kwargs)


def test_empty_filter_matches_all(rows):
    assert all(ExamFilter().matches(r) for r in rows)


def test_chunks():
    assert [len(c) for c in portal.chunks(list(range(23)))] == [10, 10, 3]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("(11057CN) E12740317416842.pdf", "(11057CN) E12740317416842.pdf"),
        ("sub/dir/a.pdf", "a.pdf"),
        ("../evil.pdf", "evil.pdf"),
        ("/abs/evil.pdf", "evil.pdf"),
        ("..\\..\\win.pdf", "win.pdf"),
        ("folder/", None),
        ("..", None),
        (".hidden", None),
    ],
)
def test_safe_member_name(name, expected):
    assert portal.safe_member_name(name) == expected


def test_extract_zip_flat_safe_and_dedup(tmp_path):
    zpath = tmp_path / "CompletedExam-1.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("(1105AAA) E1.pdf", b"one")
        zf.writestr("../escape.pdf", b"two")
        zf.writestr("dir/", b"")
    dest = tmp_path / "out"
    files = portal.extract_zip(zpath, dest)
    assert sorted(f.name for f in files) == ["(1105AAA) E1.pdf", "escape.pdf"]
    assert not (tmp_path / "escape.pdf").exists()
    assert sorted(p.name for p in dest.iterdir()) == ["(1105AAA) E1.pdf", "escape.pdf"]

    # Same content again: reused, not duplicated.
    assert sorted(f.name for f in portal.extract_zip(zpath, dest)) == ["(1105AAA) E1.pdf", "escape.pdf"]
    assert len(list(dest.iterdir())) == 2

    # Same name, different content: kept alongside with a suffix.
    z2 = tmp_path / "CompletedExam-2.zip"
    with zipfile.ZipFile(z2, "w") as zf:
        zf.writestr("(1105AAA) E1.pdf", b"rescan")
    [f] = portal.extract_zip(z2, dest)
    assert f.name == "(1105AAA) E1-1.pdf"
    assert f.read_bytes() == b"rescan"


def test_drop_session_cookies():
    state = {
        "cookies": [
            {"name": "ASP.NET_SessionId", "expires": -1},
            {"name": "ESTSAUTHPERSISTENT", "expires": 1900000000},
        ],
        "origins": [],
    }
    assert [c["name"] for c in drop_session_cookies(state)["cookies"]] == ["ESTSAUTHPERSISTENT"]
    assert len(state["cookies"]) == 2  # input untouched
