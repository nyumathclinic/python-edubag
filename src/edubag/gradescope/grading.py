"""Grading progress from Gradescope's Grading Dashboard.

Each assignment's Grade Submissions page (``/courses/<course>/assignments/<id>/grade``) embeds
a ``GradingDashboard`` React component whose props list every question of the outline with
its graded and total submission counts and the graders who have graded it. That page only
covers one assignment, so for a version set (assignment container) the dashboards of all the
versions are fetched and combined here.
"""

from __future__ import annotations

import pandas as pd

PROGRESS_COLUMNS = [
    "assignment_id",
    "question_id",
    "number",
    "title",
    "points",
    "leaf",
    "graded",
    "total",
    "graders",
]


def _outline_numbers(questions: dict[str, dict]) -> dict[str, str]:
    """Outline number ("1", "2.1", ...) of each question, keyed by question ID.

    A question's ``index`` is its position among its siblings, so its outline number is its
    parent's number followed by its index.
    """
    numbers: dict[str, str] = {}

    def number(qid: str) -> str:
        if qid not in numbers:
            q = questions[qid]
            parent = q.get("parent_id")
            own = str(q.get("index"))
            numbers[qid] = (
                f"{number(str(parent))}.{own}" if parent is not None and str(parent) in questions else own
            )
        return numbers[qid]

    for qid in questions:
        number(qid)
    return numbers


def dashboard_progress(dashboard: dict) -> pd.DataFrame:
    """Per-question grading progress of one assignment, from its Grading Dashboard.

    Args:
        dashboard: One entry of the ``GradingDashboard`` props' ``presenter.assignments``.

    Returns:
        A DataFrame with one row per question and columns ``PROGRESS_COLUMNS``. ``leaf`` marks
        the questions that are graded directly (parent questions only group their parts),
        ``graded``/``total`` count submissions, and ``graders`` is the list of names of the
        graders who have graded the question so far.
    """
    questions = {str(qid): q for qid, q in (dashboard.get("questions") or {}).items()}
    numbers = _outline_numbers(questions)
    leaves = {str(qid) for qid in dashboard.get("leaf_question_ids") or []}
    rows = [
        {
            "assignment_id": str(dashboard.get("id")),
            "question_id": qid,
            "number": numbers[qid],
            "title": q.get("title"),
            "points": pd.to_numeric(q.get("weight"), errors="coerce"),
            "leaf": qid in leaves if leaves else True,
            "graded": q.get("total_graded_count"),
            "total": q.get("total_count"),
            "graders": [g.get("name") for g in q.get("graders") or []],
        }
        for qid, q in questions.items()
    ]
    df = pd.DataFrame(rows, columns=PROGRESS_COLUMNS)
    df["leaf"] = df["leaf"].astype(bool)
    df["graded"] = df["graded"].astype("Int64")
    df["total"] = df["total"].astype("Int64")
    return df.sort_values(
        "number", key=lambda s: s.map(lambda n: tuple(int(x) for x in n.split(".")))
    ).reset_index(drop=True)
