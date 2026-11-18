"""A local record of exam files uploaded to AIM, kept outside any repository.

The ledger is a JSON file (the caller chooses where; it holds NetIDs, so keep it with the other
student files)::

    {"version": 1,
     "uploads": {"q09": {"ab1234": [{"tag": "q09-v0.2", "uploaded_at": "...", "sha256": "...",
                                     "session": "<UpcomingRow.key>", "replaced": ["q09-v0.1"]}]}}}

Each successful upload appends an entry and rewrites the file atomically (mode 0600), so an
interrupted run leaves a consistent ledger. AIM's own file list, whose titles carry the tag, is
what decides whether to upload; the ledger is the audit trail.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

LEDGER_VERSION = 1


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {"version": LEDGER_VERSION, "uploads": {}}
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if data.get("version") != LEDGER_VERSION:
                raise ValueError(f"{self.path}: unknown ledger version {data.get('version')!r}")
            self.data = data

    def entries(self, module: str, netid: str) -> list[dict]:
        return list(self.data["uploads"].get(module, {}).get(netid, []))

    def last(self, module: str, netid: str) -> dict | None:
        entries = self.entries(module, netid)
        return entries[-1] if entries else None

    def record(
        self,
        module: str,
        netid: str,
        *,
        tag: str,
        sha256: str,
        session: str,
        replaced: list[str] | None = None,
        when: datetime | None = None,
    ) -> dict:
        entry = {
            "tag": tag,
            "uploaded_at": (when or datetime.now().astimezone()).isoformat(timespec="seconds"),
            "sha256": sha256,
            "session": session,
        }
        if replaced:
            entry["replaced"] = list(replaced)
        self.data["uploads"].setdefault(module, {}).setdefault(netid, []).append(entry)
        self.save()
        return entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
