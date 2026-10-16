from __future__ import annotations

import zipfile

import pandas as pd
import pytest

from edubag.gradescope.client import GradescopeClient
from edubag.gradescope.grading import dashboard_progress
from edubag.gradescope.scoresheet import (
    VersionedScoresheet,
    parse_question_column,
    question_columns,
)

HEADER = (
    "First Name,Last Name,SID,Email,Sections,Total Score,Max Points,Status,Submission ID,"
    "Submission Time,Lateness (H:M:S),View Count,Submission Count,"
    "1: Limits (5.0 pts),2: Series (10.0 pts)"
)


def _version_set_zip(tmp_path, versions: dict[str, list[str]]):
    path = tmp_path / "Test_Free_Response_Version_Set_Scores.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Test_Free_Response_Set_Scores.csv", HEADER + "\n")
        for name, rows in versions.items():
            zf.writestr(f"{name}_scores.csv", "\n".join([HEADER, *rows]) + "\n")
    return path


def test_parse_question_column() -> None:
    assert parse_question_column("2.1: Part a (2.5 pts)") == ("2.1", "Part a", 2.5)
    assert parse_question_column("1: Ratio test: (x) (3.0 pts)") == ("1", "Ratio test: (x)", 3.0)
    assert parse_question_column("Total Score") is None


def test_question_columns_rejects_mixed_versions() -> None:
    df = pd.DataFrame(columns=["1: A (1.0 pts)", "1: B (1.0 pts)"])
    with pytest.raises(ValueError):
        question_columns(df)
    assert question_columns(pd.DataFrame(columns=["Email", "1: A (1.0 pts)"])) == {"1": "1: A (1.0 pts)"}


def test_from_zip_keeps_ungraded_question_columns(tmp_path) -> None:
    row = "Ann,Lee,1,ann@school.edu,016,{total},15.0,{status},11,2026-10-10 10:00:00 -0400,0:00:00,0,1,{q1},"
    missing = "Bo,Ng,2,bo@school.edu,016,,15.0,Missing,,,,,,,"
    path = _version_set_zip(
        tmp_path,
        {
            "Version_A": [row.format(total=4.0, status="Ungraded", q1=4.0), missing],
            "Version_B": [row.format(total=0.0, status="Ungraded", q1="").replace("ann", "cy")],
        },
    )

    ss = VersionedScoresheet.from_zip(path)
    # Question 2 is ungraded everywhere but must not vanish.
    assert "2: Series (10.0 pts)" in ss.scores.columns
    assert ss.scores["2: Series (10.0 pts)"].isna().all()
    assert list(ss.scores["Version"]) == ["Version A", "Version B"]
    assert ss.name == "Test Free Response"

    with_missing = VersionedScoresheet.from_zip(path, drop_missing=False)
    assert "bo@school.edu" in set(with_missing.scores["Email"])


def test_dashboard_progress_numbers_subquestions() -> None:
    dashboard = {
        "id": 42,
        "leaf_question_ids": [2, 3, 4],
        "questions": {
            "1": {"index": 1, "parent_id": None, "title": "Q1", "weight": "10.0",
                  "total_graded_count": 3, "total_count": 5, "graders": []},
            "2": {"index": 1, "parent_id": 1, "title": "Q1a", "weight": "4.0",
                  "total_graded_count": 5, "total_count": 5, "graders": [{"name": "TA One"}]},
            "3": {"index": 2, "parent_id": 1, "title": "Q1b", "weight": "6.0",
                  "total_graded_count": 0, "total_count": 5, "graders": []},
            "4": {"index": 2, "parent_id": None, "title": "Q2", "weight": "5.0",
                  "total_graded_count": 2, "total_count": 5,
                  "graders": [{"name": "TA One"}, {"name": "TA Two"}]},
        },
    }
    df = dashboard_progress(dashboard)
    assert list(df["number"]) == ["1", "1.1", "1.2", "2"]
    assert list(df["leaf"]) == [False, True, True, True]
    assert df.loc[df["number"] == "2", "graders"].iloc[0] == ["TA One", "TA Two"]
    assert df["points"].tolist() == [10.0, 4.0, 6.0, 5.0]
    assert (df["assignment_id"] == "42").all()


def test_container_versions_filters_and_orders() -> None:
    table = [
        {"type": "assignment_container", "id": "assignment_container_9", "url": "/courses/1/assignment_containers/9"},
        {"type": "assignment", "container_id": 9, "version_index": 2, "title": "B",
         "url": "/courses/1/assignments/102", "num_active_submissions": 3, "grading_progress": 50},
        {"type": "assignment", "container_id": 9, "version_index": 1, "title": "A",
         "url": "/courses/1/assignments/101", "num_active_submissions": 4, "grading_progress": 0},
        {"type": "assignment", "container_id": 8, "title": "Other", "url": "/courses/1/assignments/201"},
        {"type": "assignment", "container_id": None, "title": "PS", "url": "/courses/1/assignments/301"},
    ]
    versions = GradescopeClient.container_versions(table, "9")
    assert [v["id"] for v in versions] == ["101", "102"]
    assert versions[1]["grading_progress"] == 50


def test_dashboard_progress_without_questions() -> None:
    df = dashboard_progress({"id": 7, "questions": {}, "leaf_question_ids": []})
    assert df.empty
    assert df[df["leaf"]].columns.tolist() == df.columns.tolist()
