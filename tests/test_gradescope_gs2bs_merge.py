from __future__ import annotations

import pandas as pd

from edubag.gradescope import _merge_scoresheets_to_gradebook
from edubag.gradescope.scoresheet import Scoresheet


def _scoresheet(name: str, max_points: int, rows: list[tuple[str, float]]) -> Scoresheet:
    df = pd.DataFrame(
        {
            "Email": [email for email, _ in rows],
            "Total Score": [score for _, score in rows],
            "Max Points": [max_points] * len(rows),
        }
    )
    return Scoresheet(name=name, scores=df)


def test_merge_scoresheets_to_gradebook_keeps_all_assignment_columns() -> None:
    quiz = _scoresheet(
        "Quiz 1",
        10,
        [
            ("alice@school.edu", 9),
            ("bob@school.edu", 8),
        ],
    )
    hw = _scoresheet(
        "HW 1",
        20,
        [
            ("alice@school.edu", 19),
            ("carol@school.edu", 18),
        ],
    )

    merged = _merge_scoresheets_to_gradebook([quiz, hw]).grades

    assert "Username" in merged.columns
    assert "Quiz 1 Points Grade <MaxScore: 10>" in merged.columns
    assert "HW 1 Points Grade <MaxScore: 20>" in merged.columns

    alice = merged.loc[merged["Username"] == "alice"].iloc[0]
    bob = merged.loc[merged["Username"] == "bob"].iloc[0]
    carol = merged.loc[merged["Username"] == "carol"].iloc[0]

    assert alice["Quiz 1 Points Grade <MaxScore: 10>"] == 9
    assert alice["HW 1 Points Grade <MaxScore: 20>"] == 19
    assert bob["Quiz 1 Points Grade <MaxScore: 10>"] == 8
    assert pd.isna(bob["HW 1 Points Grade <MaxScore: 20>"])
    assert pd.isna(carol["Quiz 1 Points Grade <MaxScore: 10>"])
    assert carol["HW 1 Points Grade <MaxScore: 20>"] == 18
