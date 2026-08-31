"""Helpers for exploratory Playwright flow discovery.

Warning:
    These utilities support discovery tooling only. They log selector attempts
    and collect diagnostics while UI flows are being validated. Selectors and
    step logic are expected to change as Gradescope UI details evolve.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


def slugify_step_name(step_name: str) -> str:
    """Convert a step name into a filesystem-safe slug."""
    sanitized = re.sub(r"[^a-z0-9]+", "_", step_name.lower()).strip("_")
    return sanitized or "step"


@dataclass
class StepLog:
    """Structured record for a single discovery step."""

    timestamp: str
    step: str
    url: str
    title: str
    locator: str
    result: str
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary."""
        return asdict(self)


class DiscoveryLogger:
    """Collect and persist structured step logs."""

    def __init__(self) -> None:
        self._entries: list[StepLog] = []

    @property
    def entries(self) -> list[StepLog]:
        """Get all logged entries."""
        return self._entries

    def log_step(
        self,
        page: Page,
        step: str,
        locator: str,
        result: str,
        message: str = "",
    ) -> StepLog:
        """Record and emit a structured step log."""
        entry = StepLog(
            timestamp=datetime.now(UTC).isoformat(),
            step=step,
            url=page.url,
            title=page.title(),
            locator=locator,
            result=result,
            message=message,
        )
        logger.info(json.dumps(entry.to_dict()))
        self._entries.append(entry)
        return entry

    def save(self, output_path: Path) -> None:
        """Write logs to JSON."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps([entry.to_dict() for entry in self._entries], indent=2),
            encoding="utf-8",
        )


def capture_checkpoint(page: Page, artifacts_dir: Path, step_name: str) -> Path:
    """Capture a screenshot for a milestone."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = artifacts_dir / f"{slugify_step_name(step_name)}.png"
    page.screenshot(path=str(screenshot_path), full_page=True)
    return screenshot_path


def probe_locator(locator: Locator) -> dict[str, Any]:
    """Return basic diagnostics for a locator."""
    count = locator.count()
    is_visible = count > 0 and locator.first.is_visible()
    return {"count": count, "visible": is_visible}
