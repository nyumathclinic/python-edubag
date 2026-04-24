from pathlib import Path

from edubag.brightspace.attendance import AttendanceData


def test_from_file_preserves_unrecorded_student_sessions(tmp_path: Path) -> None:
    csv_path = tmp_path / "attendance.csv"
    csv_path.write_text(
        "\n".join(
            [
                "First Name,Last Name,Username,January 30,February 6,February 13,P,R,A,X,% Attendance,End-of-Line Indicator",
                "Selena,Li,zl5717,-,P,P,7,0,0,0,100,#",
                "Alex,Student,as1234,P,P,P,3,0,0,0,100,#",
            ]
        ),
        encoding="utf-8",
    )

    source = AttendanceData.from_file(csv_path)
    row = source.data.loc[source.data["Username"] == "zl5717"].iloc[0]

    assert source.metadata["sessions"] == ["January 30", "February 6", "February 13"]
    assert row["January 30"] == "-"
    assert row["P"] == 2
    assert row["R"] == 0
    assert row["A"] == 0
    assert row["X"] == 0
    assert row["% Attendance"] == 1.0


def test_from_file_drops_fully_unrecorded_sessions(tmp_path: Path) -> None:
    csv_path = tmp_path / "attendance.csv"
    csv_path.write_text(
        "\n".join(
            [
                "First Name,Last Name,Username,January 30,February 6,P,R,A,X,% Attendance,End-of-Line Indicator",
                "Selena,Li,zl5717,P,-,1,0,0,0,100,#",
                "Alex,Student,as1234,A,-,0,0,1,0,0,#",
            ]
        ),
        encoding="utf-8",
    )

    source = AttendanceData.from_file(csv_path)

    assert "February 6" not in source.data.columns
    assert source.metadata["sessions"] == ["January 30"]