#!/usr/bin/env python
"""Discovery tooling for mapping the Gradescope assignment rubric UI flow.

Warning:
    This script is intentionally exploratory. It captures diagnostics while we
    validate selectors and flow sequencing for rubric editing/upload.
    Selectors may need frequent updates as Gradescope UI changes.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import platformdirs
from dotenv import load_dotenv
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from edubag.gradescope.discovery import DiscoveryLogger, capture_checkpoint, probe_locator  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", required=True, help="Course identifier, URL, or visible course name")
    parser.add_argument(
        "--assignment",
        required=True,
        help="Assignment identifier, URL, or visible assignment name",
    )
    parser.add_argument("--term", help="Optional term text to help narrow course selection")
    parser.add_argument("--rubric-file", type=Path, help="Optional rubric file path to try uploading")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts/rubric-flow"),
        help="Directory for trace, screenshots, and structured logs",
    )
    parser.add_argument(
        "--auth-state-path",
        type=Path,
        default=Path(platformdirs.user_cache_dir("edubag", "NYU")) / "gradescope_auth.json",
        help="Path to Playwright storage state JSON",
    )
    parser.add_argument(
        "--base-url",
        default="https://gradescope.com",
        help="Gradescope base URL",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--headless", action="store_true", help="Run browser headless")
    mode_group.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        help="Run browser in headed mode (default for discovery)",
    )
    parser.set_defaults(headless=False)
    parser.add_argument(
        "--attempt-save",
        action="store_true",
        help="Attempt clicking a save/publish rubric button",
    )
    return parser


def _log_probe(flow_logger: DiscoveryLogger, page, step: str, locator_name: str, locator) -> dict:
    probe = probe_locator(locator)
    flow_logger.log_step(
        page=page,
        step=step,
        locator=locator_name,
        result="probe",
        message=f"count={probe['count']} visible={probe['visible']}",
    )
    return probe


def _click_first(
    page,
    flow_logger: DiscoveryLogger,
    step: str,
    locator_candidates: list[tuple[str, object]],
) -> bool:
    for locator_name, locator in locator_candidates:
        probe = _log_probe(flow_logger, page, step, locator_name, locator)
        if not probe["visible"]:
            continue
        try:
            locator.first.click()
            page.wait_for_load_state("networkidle")
            flow_logger.log_step(page, step, locator_name, "success", "click succeeded")
            return True
        except Exception as exc:
            flow_logger.log_step(page, step, locator_name, "failure", f"click failed: {exc}")
    flow_logger.log_step(page, step, "n/a", "failure", "no visible locator candidate matched")
    return False


def _open_course(page, base_url: str, course: str, term: str | None, flow_logger: DiscoveryLogger) -> bool:
    step = "open_course"
    if course.startswith("http://") or course.startswith("https://"):
        page.goto(course)
        page.wait_for_load_state("networkidle")
        flow_logger.log_step(page, step, "direct course URL", "success")
        return True

    if course.isdigit():
        page.goto(f"{base_url}/courses/{course}")
        page.wait_for_load_state("networkidle")
        flow_logger.log_step(page, step, "derived course URL from numeric id", "success")
        return True

    page.goto(f"{base_url}/account")
    page.wait_for_load_state("networkidle")

    locator_name = f"role=link name~/{course}/i"
    locator = page.get_by_role("link", name=re.compile(re.escape(course), re.IGNORECASE))
    probe = _log_probe(flow_logger, page, step, locator_name, locator)
    if probe["count"] == 0:
        flow_logger.log_step(page, step, locator_name, "failure", "course link not found")
        return False
    locator.first.click()
    page.wait_for_load_state("networkidle")
    message = "matched first visible course link"
    if term:
        message += f"; term hint provided: {term}"
    flow_logger.log_step(page, step, locator_name, "success", message)
    return True


def _open_assignment(page, base_url: str, assignment: str, flow_logger: DiscoveryLogger) -> bool:
    step = "open_assignment"
    if assignment.startswith("http://") or assignment.startswith("https://"):
        page.goto(assignment)
        page.wait_for_load_state("networkidle")
        flow_logger.log_step(page, step, "direct assignment URL", "success")
        return True

    current_url = page.url
    course_id_match = re.search(r"/courses/(?P<course_id>\d+)", current_url)
    if assignment.isdigit() and course_id_match:
        course_id = course_id_match.group("course_id")
        page.goto(f"{base_url}/courses/{course_id}/assignments/{assignment}")
        page.wait_for_load_state("networkidle")
        flow_logger.log_step(page, step, "derived assignment URL from numeric id", "success")
        return True

    locator_name = f"role=link name~/{assignment}/i"
    locator = page.get_by_role("link", name=re.compile(re.escape(assignment), re.IGNORECASE))
    probe = _log_probe(flow_logger, page, step, locator_name, locator)
    if probe["count"] == 0:
        flow_logger.log_step(page, step, locator_name, "failure", "assignment link not found")
        return False

    locator.first.click()
    page.wait_for_load_state("networkidle")
    flow_logger.log_step(page, step, locator_name, "success", "matched first visible assignment link")
    return True


def main() -> int:
    """Run rubric flow discovery."""
    from playwright.sync_api import TimeoutError, sync_playwright

    args = build_parser().parse_args()
    artifacts_dir = args.artifacts_dir.resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    flow_logger = DiscoveryLogger()
    trace_path = artifacts_dir / "trace.zip"
    logs_path = artifacts_dir / "step_logs.json"

    load_dotenv()
    username = os.getenv("GRADESCOPE_USERNAME")
    password = os.getenv("GRADESCOPE_PASSWORD")
    rubric_file = args.rubric_file.resolve() if args.rubric_file else None

    exit_code = 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        context = browser.new_context(
            storage_state=str(args.auth_state_path) if args.auth_state_path.exists() else None
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()

        try:
            page.goto(f"{args.base_url}/account")
            page.wait_for_load_state("networkidle")
            login_step = "login_complete"
            flow_logger.log_step(page, login_step, "goto /account", "probe")

            if "login" in page.url:
                login_candidates = [("button: Log In", page.get_by_role("button", name="Log In"))]
                _click_first(page, flow_logger, "login_open_form", login_candidates)
                email = page.get_by_role("textbox", name=re.compile("Email", re.IGNORECASE))
                password_box = page.get_by_role("textbox", name=re.compile("Password", re.IGNORECASE))
                can_fill_credentials = (
                    username
                    and password
                    and probe_locator(email)["visible"]
                    and probe_locator(password_box)["visible"]
                )
                if can_fill_credentials:
                    email.first.fill(username)
                    password_box.first.fill(password)
                    _click_first(
                        page,
                        flow_logger,
                        "login_submit",
                        [("button: Log In", page.get_by_role("button", name="Log In"))],
                    )
                try:
                    page.wait_for_url("**/account", timeout=60000)
                    flow_logger.log_step(page, login_step, "wait_for_url **/account", "success")
                except TimeoutError:
                    flow_logger.log_step(
                        page,
                        login_step,
                        "wait_for_url **/account",
                        "failure",
                        "account URL not reached; continuing with diagnostics",
                    )
                    exit_code = 1
            else:
                flow_logger.log_step(page, login_step, "existing auth_state session", "success")

            capture_checkpoint(page, artifacts_dir, "login_complete")

            course_ok = _open_course(page, args.base_url, args.course, args.term, flow_logger)
            capture_checkpoint(page, artifacts_dir, "course_opened")
            if not course_ok:
                exit_code = 1

            assignment_ok = _open_assignment(page, args.base_url, args.assignment, flow_logger)
            capture_checkpoint(page, artifacts_dir, "assignment_opened")
            if not assignment_ok:
                exit_code = 1

            rubric_opened = _click_first(
                page,
                flow_logger,
                "reach_rubric_editor",
                [
                    ("tab: Rubric", page.get_by_role("tab", name=re.compile("Rubric", re.IGNORECASE))),
                    ("link: Rubric", page.get_by_role("link", name=re.compile("Rubric", re.IGNORECASE))),
                    ("button: Edit Rubric", page.get_by_role("button", name=re.compile("Edit Rubric", re.IGNORECASE))),
                    (
                        "button: Create Rubric",
                        page.get_by_role("button", name=re.compile("Create Rubric", re.IGNORECASE)),
                    ),
                ],
            )
            capture_checkpoint(page, artifacts_dir, "rubric_editor_reached")
            if not rubric_opened:
                exit_code = 1

            upload_step = "upload_attempted"
            upload_candidates: list[tuple[str, object]] = [
                ("label: Upload Rubric", page.get_by_label(re.compile("Upload Rubric", re.IGNORECASE))),
                ("button: Upload Rubric", page.get_by_role("button", name=re.compile("Upload Rubric", re.IGNORECASE))),
                ("button: Import Rubric", page.get_by_role("button", name=re.compile("Import Rubric", re.IGNORECASE))),
                ("css: input[type='file']", page.locator("input[type='file']")),
            ]
            if rubric_file and rubric_file.exists():
                upload_complete = False
                for locator_name, locator in upload_candidates:
                    probe = _log_probe(flow_logger, page, upload_step, locator_name, locator)
                    if probe["count"] == 0:
                        continue
                    try:
                        if "input[type='file']" in locator_name:
                            locator.first.set_input_files(str(rubric_file))
                        else:
                            with page.expect_file_chooser(timeout=3000) as file_chooser_info:
                                locator.first.click()
                            file_chooser_info.value.set_files(str(rubric_file))
                        flow_logger.log_step(page, upload_step, locator_name, "success", f"set file {rubric_file.name}")
                        upload_complete = True
                        break
                    except Exception as exc:
                        flow_logger.log_step(page, upload_step, locator_name, "failure", f"upload action failed: {exc}")
                if not upload_complete:
                    flow_logger.log_step(
                        page,
                        upload_step,
                        "n/a",
                        "failure",
                        "rubric upload control not found or not usable; diagnostics captured",
                    )
                    exit_code = 1
            else:
                flow_logger.log_step(
                    page,
                    upload_step,
                    "--rubric-file",
                    "skipped",
                    "no rubric file provided or file missing; probing selectors only",
                )
                for locator_name, locator in upload_candidates:
                    _log_probe(flow_logger, page, upload_step, locator_name, locator)
            capture_checkpoint(page, artifacts_dir, "upload_attempted")

            save_step = "save_attempted"
            if args.attempt_save:
                save_clicked = _click_first(
                    page,
                    flow_logger,
                    save_step,
                    [
                        (
                            "button: Save Rubric",
                            page.get_by_role("button", name=re.compile("Save Rubric", re.IGNORECASE)),
                        ),
                        ("button: Save", page.get_by_role("button", name=re.compile("^Save$", re.IGNORECASE))),
                        ("button: Publish", page.get_by_role("button", name=re.compile("Publish", re.IGNORECASE))),
                    ],
                )
                if not save_clicked:
                    exit_code = 1
            else:
                flow_logger.log_step(
                    page,
                    save_step,
                    "--attempt-save",
                    "skipped",
                    "save click disabled by default for safe discovery runs",
                )
            capture_checkpoint(page, artifacts_dir, "save_attempted")
        finally:
            context.tracing.stop(path=str(trace_path))
            flow_logger.save(logs_path)
            browser.close()

    logger.info(f"Wrote discovery artifacts to {artifacts_dir}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
