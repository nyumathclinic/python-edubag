#!/usr/bin/env python
"""
Snapshot WebAssign pages while you click through them by hand.

Opens a headed browser signed in with the saved WebAssign auth state, then
pauses in the Playwright inspector. Walk the click path

    View (assignment) -> Edit Assignment -> View (problem) -> Open in Editor

and press Resume in the inspector when done. Every page and popup that loads
(including its frames) is saved as HTML, and all network traffic is recorded
to a HAR file, so we can find the assignment/question URLs, field names and
any JSON endpoints.

Do NOT click Save in the question editor.

Snapshots contain publisher content; keep them out of the repository.

Usage:
    uv run python tests/debug_webassign_snapshot.py OUTPUT_DIR
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from edubag.webassign.client import WebAssignClient


def _slug(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", url.split("://", 1)[-1])[:80].strip("_")


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    client = WebAssignClient()
    if not client.auth_state_path.exists():
        client.authenticate()
    counter = iter(range(1, 10**6))

    def snapshot(page: Page) -> None:
        stamp = f"{next(counter):03d}"
        for i, frame in enumerate(page.frames):
            try:
                html = frame.content()
            except Exception as e:
                print(f"  could not read frame {frame.url}: {e}")
                continue
            name = f"{stamp}-{i}-{_slug(frame.url)}.html"
            (out_dir / name).write_text(f"<!-- {frame.url} -->\n{html}")
        print(f"[{stamp}] saved {page.url} ({len(page.frames)} frame(s))")

    def watch(page: Page) -> None:
        page.on("load", lambda: snapshot(page))
        print(f"watching page {page.url or '(new)'}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=client.auth_state_path,
            record_har_path=out_dir / "traffic.har",
            record_har_content="embed",
        )
        context.on("page", watch)
        page = context.new_page()
        page.goto(f"{client.base_url}{client.login_path}")
        print("Walk the click path, then press Resume in the Playwright inspector.")
        page.pause()
        # Grab the final state of every open page, in case it changed without a load.
        for open_page in context.pages:
            snapshot(open_page)
        context.close()
        browser.close()
    print(f"Snapshots and traffic.har saved to {out_dir} at {datetime.now():%H:%M}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
