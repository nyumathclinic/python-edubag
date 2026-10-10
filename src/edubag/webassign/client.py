"""Module to automate interactions with the WebAssign platform."""

import asyncio
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import platformdirs
from dotenv import load_dotenv
from loguru import logger
from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from edubag.clients import LMSClient


def _run_sync_in_thread[T](func: Callable[..., T], *args, **kwargs) -> T:
    """Run sync Playwright code in a dedicated worker thread.

    Python 3.13 can surface event-loop ownership edge cases where checking only
    ``get_running_loop`` is insufficient. Always dispatching to a fresh thread
    with an isolated event loop avoids those ambiguities.
    """
    result_container: list = []
    exception_container: list = []

    def thread_wrapper() -> None:
        # Give this thread a fresh, isolated event loop.
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            result_container.append(func(*args, **kwargs))
        except Exception as exc:
            exception_container.append(exc)
        finally:
            new_loop.close()
            asyncio.set_event_loop(None)

    thread = threading.Thread(target=thread_wrapper, daemon=False)
    thread.start()
    thread.join()

    if exception_container:
        raise exception_container[0]
    return result_container[0]


# Sections on the Download Manager page: a radio per course, a checkbox per section.
LIST_SECTIONS_JS = """() => {
  const label = e => ((e.labels && e.labels[0] && e.labels[0].innerText) || '').trim();
  const courses = {};
  for (const e of document.querySelectorAll("input[type=radio][name=sectionList]")) courses[e.value] = label(e);
  return [...document.querySelectorAll("input[type=checkbox][name=sectionList]")].map(e => {
    const [course, section] = e.value.split(",");
    return {course_id: course, section_id: section, course: courses[course] || "", section: label(e)};
  });
}"""


class WebAssignAuthError(RuntimeError):
    """The saved WebAssign session is missing or has expired."""


# Friendly names for the Download Manager's "File Type" options (single file).
FILE_TYPES = {"csv": "csv", "tsv": "tsv", "excel": "xls"}
# Friendly names for the roster's per-section student filter.
STUDENT_FILTERS = {"current": "current_students", "dropped": "dropped_students", "all": "all"}
# Download Manager areas and the button that opens each.
DOWNLOAD_AREAS = {"roster": "Roster", "scores": "Scores", "gradebook": "GradeBook"}
# Friendly names for the scores download's per-section assignment filter.
ASSIGNMENT_FILTERS = {
    "all": "past_current_future",
    "past-current": "past_current",
    "past": "past",
    "current": "current",
    "future": "future",
}

# ScoreView's "Send Scores To LMS" link calls a function that issues this GET.
SYNC_PATH = "/web/Rest/Section/sync_scores_to_lms"
SYNC_MESSAGES = {
    200: "Scores successfully sent to the LMS",
    429: "Scores have already been manually sent to the LMS within the last hour. "
    "Class scores can only be manually sent once per hour",
}
SYNC_ERROR_MESSAGE = "An error occurred while sending the scores to the LMS"


def file_type_value(file_type: str) -> str:
    """Map a friendly file type (csv, tsv, excel) to WebAssign's option value."""
    try:
        return FILE_TYPES[file_type.lower()]
    except KeyError:
        raise ValueError(f"Unknown file type {file_type!r}; choose from {', '.join(FILE_TYPES)}") from None


def student_filter_value(students: str) -> str:
    """Map a friendly student filter (current, dropped, all) to WebAssign's option value."""
    try:
        return STUDENT_FILTERS[students.lower()]
    except KeyError:
        raise ValueError(
            f"Unknown student filter {students!r}; choose from {', '.join(STUDENT_FILTERS)}"
        ) from None


def assignment_filter_value(assignments: str) -> str:
    """Map a friendly assignment filter to WebAssign's option value."""
    try:
        return ASSIGNMENT_FILTERS[assignments.lower()]
    except KeyError:
        raise ValueError(
            f"Unknown assignment filter {assignments!r}; choose from {', '.join(ASSIGNMENT_FILTERS)}"
        ) from None


def section_id(section: str | int) -> str:
    """Normalize a section identifier to WebAssign's numeric section ID.

    Accepts the bare section ID ("1691600") or WebAssign's "course,section"
    pair ("1299802,1691600").
    """
    value = str(section).strip().rsplit(",", 1)[-1]
    if not value.isdigit():
        raise ValueError(f"Invalid WebAssign section ID: {section!r}")
    return value


def sync_message(status: int) -> str:
    """The message WebAssign shows for a sync response status."""
    return SYNC_MESSAGES.get(status, SYNC_ERROR_MESSAGE)


class WebAssignClient(LMSClient):
    """Client to interact with the WebAssign platform.

    This client provides automated browser-based interactions with Cengage
    WebAssign for downloading rosters and gradebooks and for syncing scores to
    the linked LMS.

    Note on headless parameter:
        Methods that accept `headless` parameter default to:
        - `False` for `authenticate()` - interactive login may require manual steps
        - `True` for other operations - automated operations benefit from headless mode
    """

    base_url = "https://www.webassign.net"
    # Sign-in is handled by Cengage's account service, which redirects back here.
    login_path = "/wa-auth/login"

    @staticmethod
    def _default_auth_state_path() -> Path:
        """Get the platform-appropriate default path for the auth state file."""
        cache_dir = Path(platformdirs.user_cache_dir("edubag", "NYU"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "webassign_auth.json"

    def __init__(self, base_url: str | None = None, auth_state_path: Path | None = None):
        """Initializes the WebAssignClient."""
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        if auth_state_path is not None:
            self.auth_state_path = auth_state_path
        else:
            self.auth_state_path = self._default_auth_state_path()

    def _is_signed_in_url(self, url: str) -> bool:
        """Whether a URL is a WebAssign page past the sign-in flow."""
        parsed = urlparse(url)
        return (
            parsed.netloc == urlparse(self.base_url).netloc
            and "login" not in parsed.path
            and "wa-auth" not in parsed.path
            and parsed.path not in ("", "/")
        )

    def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        headless: bool = False,
    ) -> None:
        """Log into WebAssign with a Cengage account and save the authentication state.

        Args:
            username (str | None): Cengage email or username. Falls back to
                ``WEBASSIGN_USERNAME``; if still None, enter it in the browser.
            password (str | None): Cengage password. Falls back to
                ``WEBASSIGN_PASSWORD``; if still None, enter it in the browser.
            headless (bool): Whether to run the browser in headless mode. Headless mode requires username and password.

        Raises:
            RuntimeError: If authentication fails.
        """
        load_dotenv()
        if username is None:
            username = os.getenv("WEBASSIGN_USERNAME")
        if password is None:
            password = os.getenv("WEBASSIGN_PASSWORD")

        # If username or password are not specified, browser must not be headless
        if username is None or password is None:
            headless = False

        def _do_authenticate() -> None:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context()
                page = context.new_page()
                page.goto(f"{self.base_url}{self.login_path}")

                # Cengage sign-in: username, NEXT, then password.
                username_field = page.locator("#idp-discovery-username")
                username_field.wait_for(state="visible", timeout=30000)
                if username is not None:
                    username_field.fill(username)
                    page.locator("#idp-discovery-submit").click()
                    if password is not None:
                        password_field = page.locator("input[type='password']").first
                        password_field.wait_for(state="visible", timeout=30000)
                        password_field.fill(password)
                        password_field.press("Enter")
                    else:
                        print("Please enter your password in the browser window.")
                else:
                    username_field.click()
                    print("Please enter your username and password in the browser window.")

                # Wait for the redirect back to a signed-in WebAssign page.
                deadline = time.monotonic() + 180
                while not self._is_signed_in_url(page.url):
                    if time.monotonic() > deadline:
                        browser.close()
                        raise RuntimeError("Timed out waiting for WebAssign sign-in to complete")
                    page.wait_for_timeout(500)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except PlaywrightTimeoutError:
                    pass
                logger.debug(f"Signed in; landed on {page.url}")

                context.storage_state(path=self.auth_state_path)
                logger.debug(f"Authentication state saved at {self.auth_state_path}")
                browser.close()

        _run_sync_in_thread(_do_authenticate)

    def _run_with_reauth[T](self, operation: Callable[[], T], headless: bool) -> T:
        """Run an operation with one re-authentication retry on auth expiration."""
        if not self.auth_state_path.exists():
            logger.warning(f"Auth state file not found at {self.auth_state_path}. Running authentication...")
            self.authenticate(headless=headless)

        try:
            return operation()
        except WebAssignAuthError as e:
            logger.warning(f"{e} Re-authenticating...")
            self.authenticate(headless=headless)
            return operation()

    def _open_my_classes(self, page: Page) -> None:
        """Open the instructor landing page, raising if the session has expired."""
        page.goto(f"{self.base_url}{self.login_path}")
        try:
            page.wait_for_url(self._is_signed_in_url, timeout=30000)
            page.locator("form#wa").wait_for(state="attached", timeout=30000)
        except PlaywrightTimeoutError:
            raise WebAssignAuthError("WebAssign session has expired.") from None

    def _open_download_manager(self, page: Page) -> None:
        """Go to Grades > Download Scores (WebAssign navigates by posting its main form)."""
        self._open_my_classes(page)
        with page.expect_navigation():
            page.evaluate("submitAction('downloads/index')")
        page.locator("input[name='sectionList']").first.wait_for(state="attached", timeout=30000)

    def _list_sections_session(self, headless: bool = True) -> list[dict[str, str]]:
        """Internal method to list sections in a single browser session."""

        def _do_list() -> list[dict[str, str]]:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(storage_state=self.auth_state_path)
                    page = context.new_page()
                    self._open_download_manager(page)
                    return page.evaluate(LIST_SECTIONS_JS)
                finally:
                    browser.close()

        return _run_sync_in_thread(_do_list)

    def list_sections(self, headless: bool = True) -> list[dict[str, str]]:
        """List the current classes' sections offered by the Download Manager.

        Returns:
            list[dict[str, str]]: One dict per section with keys ``course_id``,
            ``section_id``, ``course`` (e.g. "MATH-UA 122, Fall 2026") and
            ``section`` (e.g. "001").
        """
        return self._run_with_reauth(lambda: self._list_sections_session(headless), headless=headless)

    def _download_from_manager(
        self,
        kind: str,
        sections: list[str],
        save_dir: Path | None,
        students: str,
        include_faculty: bool,
        file_type: str,
        headless: bool,
        assignments: str = "all",
    ) -> Path:
        """Download a roster, scores or gradebook through the Download Manager.

        Raises WebAssignAuthError if authentication has expired.
        """
        button = DOWNLOAD_AREAS[kind]
        filetype = file_type_value(file_type)
        student_filter = student_filter_value(students)
        assignment_filter = assignment_filter_value(assignments)
        ids = [section_id(s) for s in sections]
        if not ids:
            raise ValueError("At least one section is required")

        def _do_download() -> Path:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(storage_state=self.auth_state_path, accept_downloads=True)
                    page = context.new_page()
                    self._open_download_manager(page)

                    # Tick the section(s); WebAssign only downloads within one course.
                    courses = set()
                    for sid in ids:
                        box = page.locator(f"input[type='checkbox'][name='sectionList'][value$=',{sid}']")
                        if box.count() == 0:
                            raise ValueError(f"Section {sid} is not among the current classes in WebAssign")
                        courses.add(box.first.get_attribute("value").split(",")[0])
                        box.first.check()
                    if len(courses) > 1:
                        raise ValueError("All sections must belong to the same WebAssign course")

                    with page.expect_navigation():
                        page.locator("button.sButton", has_text=button).click()
                    page.locator("#filetype").wait_for(state="visible", timeout=30000)

                    # The GradeBook download has no per-section student options.
                    if kind in ("roster", "scores"):
                        for sid in ids:
                            if kind == "scores":
                                page.locator(f"#assignments_{sid}").select_option(assignment_filter)
                            page.locator(f"#students_{sid}").select_option(student_filter)
                            page.locator(f"#faculty_{sid}_y").set_checked(include_faculty)
                    if kind in ("roster", "scores"):
                        # Include every student detail (username, institution, student number, ...).
                        for box in page.locator("form#wa input[type='checkbox'][name='studentInfo']").all():
                            box.check()
                    if kind == "scores":
                        # Include every assignment detail (due date, category, assignment id).
                        for box in page.locator("form#wa input[type='checkbox'][name='assignmentInfo']").all():
                            box.check()
                    try:
                        page.locator("#filetype").select_option(filetype, timeout=5000)
                    except PlaywrightTimeoutError:
                        raise ValueError(
                            f"WebAssign does not offer file type {file_type!r} for this selection"
                        ) from None

                    download_button = page.locator("form#wa").get_by_role("button", name="Download", exact=True)
                    if download_button.is_disabled():
                        # e.g. "Your GradeBook has not been set up for this class."
                        note = page.locator("form#wa").get_by_text(re.compile(r"^\s*\*\s*\S")).first
                        reason = note.inner_text().strip().lstrip("* ") if note.count() else "Download is disabled"
                        raise RuntimeError(f"WebAssign will not download the {kind}: {reason}")
                    with page.expect_download() as download_info:
                        download_button.click()
                    download = download_info.value
                    target_dir = save_dir or Path.cwd()
                    target_dir.mkdir(parents=True, exist_ok=True)
                    path = target_dir / download.suggested_filename
                    download.save_as(path)
                    logger.info(f"Saved WebAssign {kind} to {path}")
                    return path
                finally:
                    browser.close()

        return _run_sync_in_thread(_do_download)

    def save_roster(
        self,
        sections: list[str],
        save_dir: Path | None = None,
        students: str = "current",
        include_faculty: bool = False,
        file_type: str = "csv",
        headless: bool = True,
    ) -> Path:
        """Download the roster for one or more sections of a course.

        Args:
            sections (list[str]): WebAssign section IDs (see `list_sections`).
            save_dir (Path | None): Directory to save into. Defaults to the current directory.
            students (str): "current", "dropped" or "all".
            include_faculty (bool): Include faculty with student access.
            file_type (str): "csv", "tsv" or "excel".
            headless (bool): Whether to run the browser in headless mode.

        Returns:
            Path: Path to the downloaded file.
        """
        return self._run_with_reauth(
            lambda: self._download_from_manager(
                "roster", sections, save_dir, students, include_faculty, file_type, headless
            ),
            headless=headless,
        )

    def save_scores(
        self,
        sections: list[str],
        save_dir: Path | None = None,
        assignments: str = "all",
        students: str = "current",
        include_faculty: bool = False,
        file_type: str = "csv",
        headless: bool = True,
    ) -> Path:
        """Download assignment scores for one or more sections of a course.

        Args:
            sections (list[str]): WebAssign section IDs (see `list_sections`).
            save_dir (Path | None): Directory to save into. Defaults to the current directory.
            assignments (str): "all", "past-current", "past", "current" or "future".
            students (str): "current", "dropped" or "all".
            include_faculty (bool): Include faculty with student access.
            file_type (str): "csv", "tsv" or "excel".
            headless (bool): Whether to run the browser in headless mode.

        Returns:
            Path: Path to the downloaded file.
        """
        return self._run_with_reauth(
            lambda: self._download_from_manager(
                "scores", sections, save_dir, students, include_faculty, file_type, headless, assignments
            ),
            headless=headless,
        )

    def save_gradebook(
        self,
        sections: list[str],
        save_dir: Path | None = None,
        file_type: str = "csv",
        headless: bool = True,
    ) -> Path:
        """Download the GradeBook for one or more sections of a course.

        The class's GradeBook must be set up in WebAssign; otherwise use `save_scores`.

        Args:
            sections (list[str]): WebAssign section IDs (see `list_sections`).
            save_dir (Path | None): Directory to save into. Defaults to the current directory.
            file_type (str): "csv", "tsv" or "excel".
            headless (bool): Whether to run the browser in headless mode.

        Returns:
            Path: Path to the downloaded file.
        """
        return self._run_with_reauth(
            lambda: self._download_from_manager("gradebook", sections, save_dir, "current", False, file_type, headless),
            headless=headless,
        )

    def _block_sync_requests(self, context: BrowserContext) -> None:
        """Abort any sync request, so a dry run cannot send scores."""
        context.route(f"**{SYNC_PATH}*", lambda route: route.abort())

    def _sync_scores_session(self, section: str, dry_run: bool, headless: bool, state: dict) -> dict:
        """Internal method to sync scores in a single browser session.

        ``state`` records whether the sync was already requested, so a
        re-authentication retry never sends it twice.

        Raises WebAssignAuthError if authentication has expired.
        """
        sid = section_id(section)

        def _do_sync() -> dict:
            if state.get("result"):
                return state["result"]
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(storage_state=self.auth_state_path)
                    if dry_run:
                        self._block_sync_requests(context)
                    page = context.new_page()
                    self._open_my_classes(page)

                    scoreview = page.locator(f"a[href*=\"grades/scores\"][href*=\",{sid}'\"]")
                    if scoreview.count() == 0:
                        raise ValueError(f"Section {sid} is not among the current classes in WebAssign")
                    with page.expect_navigation():
                        scoreview.first.click()

                    send = page.locator(f"a[href*='manuallySendScoresToLms({sid})']")
                    try:
                        send.first.wait_for(state="visible", timeout=30000)
                    except PlaywrightTimeoutError:
                        raise RuntimeError(
                            f"No 'Send Scores To LMS' link in ScoreView for section {sid}; "
                            "is the class linked to an LMS?"
                        ) from None
                    if dry_run:
                        logger.info(f"Dry run: found 'Send Scores To LMS' for section {sid}; not clicking")
                        return {"section": sid, "sent": False, "dry_run": True, "message": "Send Scores To LMS found"}

                    with page.expect_response(lambda r: SYNC_PATH in r.url, timeout=120000) as response_info:
                        send.first.click()
                    status = response_info.value.status
                    state["result"] = {
                        "section": sid,
                        "sent": status == 200,
                        "dry_run": False,
                        "status": status,
                        "message": sync_message(status),
                    }
                    return state["result"]
                finally:
                    browser.close()

        return _run_sync_in_thread(_do_sync)

    def sync_scores(self, section: str, dry_run: bool = False, headless: bool = True) -> dict:
        """Send a section's scores from WebAssign to the linked LMS.

        This is ScoreView's "Send Scores To LMS". WebAssign allows one manual
        sync per section per hour, and a sync can overwrite score edits made in
        the LMS gradebook.

        Args:
            section (str): WebAssign section ID (see `list_sections`).
            dry_run (bool): Locate the link but do not click it.
            headless (bool): Whether to run the browser in headless mode.

        Returns:
            dict: ``section``, ``sent``, ``dry_run``, ``message`` and, for a real run, ``status``.
        """
        state: dict = {}
        return self._run_with_reauth(
            lambda: self._sync_scores_session(section, dry_run, headless, state), headless=headless
        )
