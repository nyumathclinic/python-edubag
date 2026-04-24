from pathlib import Path
from typing import List

import pandas as pd
from loguru import logger

from edubag.sources import DataSource


class AttendanceData(DataSource):
    """Brightspace attendance data source."""

    statuses = ["P", "R", "A", "X"]  # Present, Remote, Absent, Excused

    @classmethod
    def from_file(cls, path: Path) -> "AttendanceData":
        """Load a Brightspace attendance CSV.

        The CSV is expected to have columns like:

        * First Name
        * Last Name
        * Username
        * One column for each attendance session, with values:
          - "P" for Present
          - "R" for Remote
          - "A" for Absent
          - "X" for Excused
          - "-" for N/A (not recorded)
        * summary columns for each status (e.g., "P", "R", "A", "X")
        * "% Attendance"
        * "End-of-Line Indicator"
        """
        identifier_colnames = ["First Name", "Last Name", "Username"]
        score_colname = "% Attendance"
        eol_colname = "End-of-Line Indicator"
        # Use a plain dict for dtype mapping; defaultdict(str) can yield
        # empty-string dtypes ("") which pandas/numpy cannot interpret.
        dtype = {}
        # Read as string so stray '-' values don't fail; we compute score later
        dtype[score_colname] = str
        dtype[eol_colname] = str
        for status in cls.statuses:
            dtype[status] = int
        for col in identifier_colnames:
            dtype[col] = str
        logger.info(f"Loading attendance data from {path}")
        df = pd.read_csv(path, dtype=dtype)

        # drop the end-of-line indicator column if present
        if eol_colname in df.columns:
            del df[eol_colname]
        # strip whitespace from column names
        df.columns = [c.strip() for c in df.columns]

        session_candidates = [
            col
            for col in df.columns
            if col not in identifier_colnames + cls.statuses + [score_colname]
        ]
        sessions: List[str] = []
        for col in session_candidates:
            # If the entire column is unrecorded, drop it. Per-student "-"
            # values remain unrecorded and are excluded from attendance counts.
            normalized = (
                df[col]
                .astype(str)
                .str.strip()
                .replace({"nan": "", "None": ""})
            )
            if normalized.replace("", "-").eq("-").all():
                logger.info(f"Dropping unrecorded session column: {col}")
                del df[col]
            else:
                df[col] = normalized
                sessions.append(col)

        # update the status count columns
        for status in cls.statuses:
            if sessions:
                df[status] = df[sessions].eq(status).sum(axis=1).astype(int)
            else:
                df[status] = 0

        # update the score column
        # score = (P + 0.5*R)/(P+R+A) [ X are ignored ]
        def compute_score(row: pd.Series) -> float:
            present = row["P"]
            remote = row["R"]
            absent = row["A"]
            total = present + remote + absent
            if total == 0:
                return 0.0
            return (present + 0.5 * remote) / total
        
        df[score_colname] = df.apply(compute_score, axis=1)

        # build return object
        obj = cls()
        obj.data = df
        obj.metadata = {
            "source": str(path),
            "type": "attendance",
            "original_columns": list(df.columns),
            "sessions": sessions,
        }
        return obj

    def resolve_identity(self, username_col: str = "Username") -> None:
        """Normalize to Username; derive from Email if needed.

        Args:
            username_col (str): Target column name (default "Username").
        """
        if username_col not in self.data.columns:
            if "Email" in self.data.columns:
                self.data[username_col] = (
                    self.data["Email"].astype(str).str.split("@").str[0]
                )
            else:
                raise ValueError(
                    "Attendance data must have 'Username' or 'Email' column"
                )
        self.metadata["username_col"] = username_col
