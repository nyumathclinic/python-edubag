"""Module to automate interactions with the Poll Everywhere platform."""

import asyncio
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar

import platformdirs
from loguru import logger
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from edubag.clients import LMSClient

T = TypeVar("T")

_ASSIGNMENT_ROW_ID_RE = re.compile(r"lms_lti_advantage_assignment_(\d+)")
_CLASS_ROW_ID_RE = re.compile(r"lms_lti_advantage_user_connection_(\d+)")


def _run_sync_in_thread(func: Callable[..., T], *args, **kwargs) -> T:
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


def _parse_timestamp(text: str | None) -> datetime | None:
    """Parse a Poll Everywhere timestamp such as ``09/29/26 04:03 PM``.

    Args:
        text: The raw text content of a "last sync" table cell.

    Returns:
        The parsed datetime, or None if the text is empty or unparseable
        (e.g. the class/assignment has never been synced).
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%m/%d/%y %I:%M %p")
    except ValueError:
        logger.debug(f"Could not parse Poll Everywhere timestamp: {text!r}")
        return None


@dataclass
class Class:
    """A course connected to Poll Everywhere via the LMS (Brightspace) integration.

    Attributes:
        id: Poll Everywhere identifier for the LMS course connection.
        name: Name of the course, as shown on the Courses page.
        start: Optional course start date.
        end: Optional course end date.
        last_roster_sync: The last time the roster was synced to the LMS.
    """

    id: int
    name: str
    start: date | None = None
    end: date | None = None
    last_roster_sync: datetime | None = None


@dataclass
class Assignment:
    """An assignment (Poll Everywhere activity set) that can be synced to the LMS gradebook.

    Attributes:
        id: Poll Everywhere identifier for the assignment.
        name: Name of the assignment.
        last_grade_sync: The last time the grades were synced to the LMS.
        class_id: Identifier of the `Class` the assignment belongs to. This is
            not part of Poll Everywhere's own data model for an assignment, but
            is needed to locate the assignment's row in its course's Gradebook
            page in order to fetch or sync it. It is set automatically by
            `Client.fetch_assignment` and `Client.sync_all_assignments_to_lms`.
    """

    id: int
    name: str
    last_grade_sync: datetime | None = None
    class_id: int | None = None


class Client(LMSClient):
    """Client to interact with the Poll Everywhere platform.

    This client provides automated browser-based interactions with Poll
    Everywhere's LMS integration (Brightspace via LTI Advantage) for syncing
    rosters and assignment grades.

    Note on headless parameter:
        Methods that accept `headless` parameter default to:
        - `False` for `authenticate()` - interactive login with SSO and MFA required
        - `True` for other operations - automated operations benefit from headless mode
    """

    base_url = "https://www.polleverywhere.com"

    @staticmethod
    def _default_auth_state_path() -> Path:
        """Get the platform-appropriate default path for the auth state file."""
        cache_dir = Path(platformdirs.user_cache_dir("edubag", "NYU"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "polleverywhere_auth.json"

    def __init__(self, base_url: str | None = None, auth_state_path: Path | None = None):
        """Initializes the Client."""
        if base_url is not None:
            self.base_url = base_url
        if auth_state_path is not None:
            self.auth_state_path = auth_state_path
        else:
            self.auth_state_path = self._default_auth_state_path()

    @property
    def courses_url(self) -> str:
        """URL of the Courses page listing all connected classes."""
        return f"{self.base_url}/lms/lti_advantage/user_connections"

    def _class_url(self, class_id: int) -> str:
        """URL of a single connected class.

        This is the same page that shows the class's Gradebook (with its
        assignments and "Sync Grades" buttons) and its "Sync Roster" button.
        """
        return f"{self.courses_url}/{class_id}"

    def authenticate(self, username: str | None = None, password: str | None = None, headless: bool = False) -> None:
        """Log into Poll Everywhere via NYU SSO and save the authentication state.

        Poll Everywhere's sign-in flow is: enter an email address, then (on a
        second page) click the "Log in with New York University" button, which
        redirects to NYU's standard SSO login (username/password) followed by
        Duo MFA approval.

        Args:
            username (str | None): NYU email address to log in with. If None, user must enter manually in browser.
            password (str | None): Password for login. If None, user must enter manually in browser.
            headless (bool): Whether to run the browser in headless mode. Headless mode requires username and password.

        Raises:
            RuntimeError: If authentication fails.
        """
        if username is None or password is None:
            headless = False

        def _do_authenticate() -> None:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context()
                page = context.new_page()

                page.goto(self.base_url)
                page.wait_for_load_state("domcontentloaded", timeout=10000)

                email_field = page.locator("input#email")
                email_field.wait_for(state="visible", timeout=10000)

                if username is not None:
                    email_field.fill(username)
                    page.get_by_role("button", name="Continue").click()
                    page.wait_for_load_state("domcontentloaded", timeout=10000)

                    sso_button = page.get_by_role("button", name="Log in with New York University")
                    sso_button.wait_for(state="visible", timeout=10000)
                    sso_button.click()

                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    page.locator("input[type='email']").wait_for(state="visible", timeout=10000)
                    page.locator("input[type='email']").fill(username)
                    page.get_by_role("button", name="Next").click()

                    if password is not None:
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                        page.locator("input[type='password']").wait_for(state="visible", timeout=10000)
                        page.locator("input[type='password']").fill(password)
                        page.get_by_role("button", name="Sign in").click()
                        page.get_by_role("button", name="Approve with MFA (Duo)‎ You").click()
                    else:
                        print("Please enter your password in the browser window and complete MFA.")
                else:
                    print(
                        "Please enter your email address in the browser window, click "
                        "'Log in with New York University', and complete NYU SSO login and MFA."
                    )

                # Microsoft SSO occasionally inserts a "Stay signed in?" interstitial.
                # If present, check "Don't show this again" and continue.
                kmsi_heading = page.locator("div[role='heading']", has_text="Stay signed in?")
                kmsi_checkbox = page.locator("#KmsiCheckboxField")
                kmsi_submit = page.locator("#idSIButton9")
                kmsi_seen = False

                try:
                    kmsi_heading.wait_for(state="visible", timeout=15000)
                    kmsi_seen = True
                except PlaywrightTimeoutError:
                    try:
                        kmsi_checkbox.wait_for(state="visible", timeout=15000)
                        kmsi_seen = True
                    except PlaywrightTimeoutError:
                        kmsi_seen = False

                if kmsi_seen:
                    if kmsi_checkbox.count() > 0 and kmsi_checkbox.is_visible() and not kmsi_checkbox.is_checked():
                        kmsi_checkbox.check()
                    kmsi_submit.click()
                    logger.debug("Handled 'Stay signed in?' interstitial during Poll Everywhere authentication")

                # SSO and MFA redirects can be slow; wait for the Poll Everywhere landing page.
                page.wait_for_url(
                    re.compile(r".*polleverywhere\.com/.*"),
                    timeout=120000,
                    wait_until="domcontentloaded",
                )

                context.storage_state(path=self.auth_state_path)
                logger.debug(f"Authentication state saved at {self.auth_state_path}")

                browser.close()

        _run_sync_in_thread(_do_authenticate)

    @staticmethod
    def _check_authenticated(page) -> None:
        """Raise if the current page indicates the auth session has expired."""
        if "/sign_in" in page.url or "/login" in page.url:
            raise RuntimeError("Authentication session expired. Please re-authenticate.")

    def fetch_class(self, id: int, headless: bool = True) -> Class:
        """Fetch details about a connected class from the Courses page.

        Args:
            id: Poll Everywhere identifier for the class (LMS course connection).
            headless: Whether to run the browser in headless mode.

        Returns:
            The `Class` details.

        Raises:
            RuntimeError: If the class cannot be found, or authentication expired.
        """

        def _do_fetch() -> Class:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()
                page.goto(self.courses_url)
                self._check_authenticated(page)

                try:
                    row = page.locator(f"tr#lms_lti_advantage_user_connection_{id}")
                    row.wait_for(state="attached", timeout=15000)

                    name = (row.locator("a").first.text_content() or "").strip()
                    last_roster_sync = _parse_timestamp(row.locator("td").nth(1).text_content())

                    start_input = row.locator(
                        "input[name='lms_lti_advantage_user_connection[platform_course_start_date]']"
                    )
                    end_input = row.locator(
                        "input[name='lms_lti_advantage_user_connection[platform_course_end_date]']"
                    )
                    start_value = start_input.get_attribute("value") if start_input.count() > 0 else None
                    end_value = end_input.get_attribute("value") if end_input.count() > 0 else None

                    return Class(
                        id=id,
                        name=name,
                        start=date.fromisoformat(start_value) if start_value else None,
                        end=date.fromisoformat(end_value) if end_value else None,
                        last_roster_sync=last_roster_sync,
                    )
                except PlaywrightTimeoutError as e:
                    raise RuntimeError(f"Class {id} was not found on the Courses page: {e}") from e
                finally:
                    browser.close()

        return _run_sync_in_thread(_do_fetch)

    def _list_class_ids(self, page) -> list[int]:
        """List the Poll Everywhere ids of all classes connected on the Courses page."""
        rows = page.locator("tr[id^='lms_lti_advantage_user_connection_']")
        ids: list[int] = []
        for i in range(rows.count()):
            row_id = rows.nth(i).get_attribute("id") or ""
            match = _CLASS_ROW_ID_RE.match(row_id)
            if match:
                ids.append(int(match.group(1)))
        return ids

    def fetch_assignment(self, id: int, class_id: int | None = None, headless: bool = True) -> Assignment:
        """Fetch details about an assignment from its class's Gradebook page.

        Args:
            id: Poll Everywhere identifier for the assignment.
            class_id: Poll Everywhere identifier of the `Class` that owns the
                assignment. If omitted, every connected class is searched (in
                the order it appears on the Courses page) until the assignment
                is found, which is slower but does not require knowing the
                owning class in advance.
            headless: Whether to run the browser in headless mode.

        Returns:
            The `Assignment` details.

        Raises:
            RuntimeError: If the assignment cannot be found, or authentication expired.
        """

        def _fetch_from_class(page, target_class_id: int) -> Assignment | None:
            page.goto(self._class_url(target_class_id))
            self._check_authenticated(page)
            row = page.locator(f"tr#lms_lti_advantage_assignment_{id}")
            try:
                row.wait_for(state="attached", timeout=8000)
            except PlaywrightTimeoutError:
                return None

            cells = row.locator("td")
            name = (cells.nth(0).text_content() or "").strip()
            last_grade_sync = _parse_timestamp(cells.nth(1).text_content())
            return Assignment(id=id, name=name, last_grade_sync=last_grade_sync, class_id=target_class_id)

        def _do_fetch() -> Assignment:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()

                try:
                    if class_id is not None:
                        assignment = _fetch_from_class(page, class_id)
                        if assignment is None:
                            raise RuntimeError(f"Assignment {id} was not found in class {class_id}.")
                        return assignment

                    page.goto(self.courses_url)
                    self._check_authenticated(page)
                    for candidate_class_id in self._list_class_ids(page):
                        assignment = _fetch_from_class(page, candidate_class_id)
                        if assignment is not None:
                            return assignment

                    raise RuntimeError(f"Assignment {id} was not found in any connected class.")
                finally:
                    browser.close()

        return _run_sync_in_thread(_do_fetch)

    def sync_roster_to_lms(self, cls: Class, headless: bool = True) -> None:
        """Sync a class's roster to the LMS, as if clicking "Sync Roster" on the Courses page.

        Args:
            cls: The `Class` whose roster should be synced.
            headless: Whether to run the browser in headless mode.

        Raises:
            RuntimeError: If the sync button cannot be found, or authentication expired.
        """

        def _do_sync() -> None:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()
                page.goto(self._class_url(cls.id))
                self._check_authenticated(page)

                try:
                    sync_button = page.locator("#sync_roster_button button")
                    sync_button.wait_for(state="visible", timeout=15000)
                    sync_button.click()
                    page.wait_for_load_state("networkidle")
                    logger.info(f"Synced roster for class {cls.id} ({cls.name})")
                except PlaywrightTimeoutError as e:
                    raise RuntimeError(f"Could not find the Sync Roster button for class {cls.id}: {e}") from e
                finally:
                    browser.close()

        _run_sync_in_thread(_do_sync)

    def sync_assignment_to_lms(
        self, assignment: Assignment, class_id: int | None = None, headless: bool = True
    ) -> None:
        """Sync an assignment's grades to the LMS, as if clicking "Sync Grades" on the Gradebook page.

        Args:
            assignment: The `Assignment` whose grades should be synced.
            class_id: Poll Everywhere identifier of the assignment's `Class`.
                Defaults to `assignment.class_id` (set by `fetch_assignment`).
            headless: Whether to run the browser in headless mode.

        Raises:
            ValueError: If no class id is provided or available on the assignment.
            RuntimeError: If the assignment/sync button cannot be found, is
                disabled (no activities), or authentication expired.
        """
        resolved_class_id = class_id if class_id is not None else assignment.class_id
        if resolved_class_id is None:
            raise ValueError(
                "class_id must be provided, or assignment.class_id must be set (e.g. via fetch_assignment)."
            )

        def _do_sync() -> None:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()
                page.goto(self._class_url(resolved_class_id))
                self._check_authenticated(page)

                try:
                    row = page.locator(f"tr#lms_lti_advantage_assignment_{assignment.id}")
                    row.wait_for(state="attached", timeout=15000)

                    sync_button = row.locator("form[action$='/sync'] button")
                    if sync_button.count() == 0:
                        raise RuntimeError(
                            f"Could not find the Sync Grades button for assignment {assignment.id}."
                        )
                    if sync_button.get_attribute("disabled") is not None:
                        raise RuntimeError(
                            f"Assignment {assignment.id} ({assignment.name}) has no activities "
                            "and cannot be synced."
                        )

                    sync_button.click()
                    page.wait_for_load_state("networkidle")
                    logger.info(f"Synced grades for assignment {assignment.id} ({assignment.name})")
                except PlaywrightTimeoutError as e:
                    raise RuntimeError(
                        f"Could not find assignment {assignment.id} in class {resolved_class_id}: {e}"
                    ) from e
                finally:
                    browser.close()

        _run_sync_in_thread(_do_sync)

    def sync_all_assignments_to_lms(self, cls: Class, headless: bool = True) -> list[Assignment]:
        """Sync all eligible assignments in a class to the LMS.

        Assignments with no activities have a disabled "Sync Grades" button in
        the Poll Everywhere UI and are skipped, matching that behavior.

        Args:
            cls: The `Class` whose assignments should be synced.
            headless: Whether to run the browser in headless mode.

        Returns:
            The list of `Assignment`s that were synced (skipped assignments are omitted).

        Raises:
            RuntimeError: If authentication expired.
        """

        def _do_sync() -> list[Assignment]:
            synced: list[Assignment] = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()
                page.goto(self._class_url(cls.id))
                self._check_authenticated(page)

                try:
                    # Gather eligible assignments into a plain list first, since
                    # clicking "Sync Grades" reloads/morphs the table and would
                    # invalidate a live, index-based locator collection.
                    eligible: list[tuple[int, str]] = []
                    rows = page.locator("tr[id^='lms_lti_advantage_assignment_']")
                    for i in range(rows.count()):
                        row = rows.nth(i)
                        row_id = row.get_attribute("id") or ""
                        match = _ASSIGNMENT_ROW_ID_RE.match(row_id)
                        if not match:
                            continue
                        assignment_id = int(match.group(1))

                        sync_button = row.locator("form[action$='/sync'] button")
                        if sync_button.count() == 0 or sync_button.get_attribute("disabled") is not None:
                            logger.debug(f"Skipping assignment {assignment_id}: not eligible for sync")
                            continue

                        cells = row.locator("td")
                        name = (cells.nth(0).text_content() or "").strip()
                        eligible.append((assignment_id, name))

                    for assignment_id, name in eligible:
                        sync_button = page.locator(
                            f"tr#lms_lti_advantage_assignment_{assignment_id} form[action$='/sync'] button"
                        )
                        sync_button.click()
                        page.wait_for_load_state("networkidle")
                        assignment = Assignment(id=assignment_id, name=name, class_id=cls.id)
                        synced.append(assignment)
                        logger.info(f"Synced grades for assignment {assignment_id} ({name})")
                finally:
                    browser.close()
            return synced

        return _run_sync_in_thread(_do_sync)
