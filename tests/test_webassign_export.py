"""Tests for parsing WebAssign roster and scores exports. All data is synthetic."""

import csv
import json

import pytest

from edubag.webassign.export import WebAssignRoster, WebAssignScores, read_blocks

COURSE = '''"MATH-UA 999, section 001+002, Fall 2026"
"Pat Q Instructor"
"Saturday, October 10, 2026  09:51 AM EDT"
""
'''

ROSTER = COURSE + '''Fullname,Username,Institution,"Student Number",Email,Nickname,
"Doe, Jane",jd0001,nyu.edu,,jd0001@nyu.edu,,
"Roe, Richard",rr0002,nyu.edu,N00000002,rr0002@nyu.edu,Rich,
'''

SCORES = COURSE + '''"Assignment Name",,,,,Total,"Chap 5.1&ndash;5.4","Chap 5.5","Quiz","Quiz",
Due,,,,,,"Sep 18 2026 11:00 AM EDT","Sep 25 2026 11:00 AM EDT","Dec 1 2026 11:00 AM EST",,
Category,,,,,,Homework,Homework,Lab,Lab,
"Assignment ID",,,,,,101,102,103,104,
Totals,,,,,30,10,5,7.5,7.5,
""
Fullname,Username,Institution,"Student Number",Email,
"Doe, Jane",jd0001,nyu.edu,,jd0001@nyu.edu,22.5,10,5,ND,
"Roe, Richard",rr0002,nyu.edu,N00000002,rr0002@nyu.edu,12,8,,4,0
""
"Not, Astudent",xx9999,nyu.edu,,xx9999@nyu.edu,1,1,1,1,1
'''


@pytest.fixture
def roster_path(tmp_path):
    path = tmp_path / "roster_1234567890.csv"
    path.write_text(ROSTER)
    return path


@pytest.fixture
def scores_path(tmp_path):
    path = tmp_path / "scores_1234567890.csv"
    path.write_text(SCORES)
    return path


def test_read_blocks_splits_on_quoted_blank_rows(scores_path):
    assert [len(b) for b in read_blocks(scores_path)] == [3, 5, 3, 1]


def test_roster_course(roster_path):
    course = WebAssignRoster.from_csv(roster_path).course
    assert course == {
        "title": "MATH-UA 999, section 001+002, Fall 2026",
        "course": "MATH-UA 999",
        "section": "001+002",
        "term": "Fall 2026",
        "instructor": "Pat Q Instructor",
        "downloaded": "2026-10-10T09:51:00-04:00",
        "downloaded_raw": "Saturday, October 10, 2026  09:51 AM EDT",
    }


def test_roster_csv_and_json(roster_path, tmp_path):
    roster = WebAssignRoster.from_csv(roster_path)
    out_csv = roster.to_csv(tmp_path / "out" / "roster_1.csv")
    rows = list(csv.reader(out_csv.open()))
    # Trailing empty header column dropped; only the student block written.
    assert rows[0] == ["Fullname", "Username", "Institution", "Student Number", "Email", "Nickname"]
    assert rows[1] == ["Doe, Jane", "jd0001", "nyu.edu", "", "jd0001@nyu.edu", ""]
    assert len(rows) == 3

    data = json.loads(roster.to_json(tmp_path / "out" / "roster_1.json").read_text())
    assert data["course"]["section"] == "001+002"
    assert data["students"][0]["Student Number"] is None
    assert data["students"][1]["Nickname"] == "Rich"


def test_scores_assignments(scores_path):
    scores = WebAssignScores.from_csv(scores_path)
    assert scores.course["total_points"] == 30
    first, _, third, fourth = scores.assignments
    assert first == {
        "name": "Chap 5.1–5.4",  # &ndash; decoded
        "due": "2026-09-18T11:00:00-04:00",
        "category": "Homework",
        "assignment_id": 101,
        "points": 10,
    }
    assert third["due"] == "2026-12-01T11:00:00-05:00"
    assert fourth["due"] is None and fourth["points"] == 7.5


def test_scores_csv_names_score_columns(scores_path, tmp_path):
    scores = WebAssignScores.from_csv(scores_path)
    rows = list(csv.reader(scores.to_csv(tmp_path / "scores_1.csv").open()))
    assert rows[0] == [
        "Fullname", "Username", "Institution", "Student Number", "Email", "Total",
        "Chap 5.1–5.4", "Chap 5.5", "Quiz [103]", "Quiz [104]",
    ]
    assert rows[1] == ["Doe, Jane", "jd0001", "nyu.edu", "", "jd0001@nyu.edu", "22.5", "10", "5", "ND", ""]
    # Rows after the student block's blank separator are not students.
    assert [r[1] for r in rows[1:]] == ["jd0001", "rr0002"]


def test_scores_json(scores_path, tmp_path):
    data = json.loads(WebAssignScores.from_csv(scores_path).to_json(tmp_path / "s.json").read_text())
    assert [a["assignment_id"] for a in data["assignments"]] == [101, 102, 103, 104]
    jane, richard = data["students"]
    assert jane["Username"] == "jd0001" and jane["Student Number"] is None
    assert jane["total"] == 22.5
    assert jane["scores"] == {"101": 10, "102": 5, "103": "ND", "104": None}
    assert richard["scores"] == {"101": 8, "102": None, "103": 4, "104": 0}
