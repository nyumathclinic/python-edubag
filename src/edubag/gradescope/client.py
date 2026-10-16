"""Module to automate interactions with the Gradescope platform."""

import asyncio
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import urljoin

import platformdirs
from dotenv import load_dotenv
from loguru import logger
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from edubag.albert.term import Term
from edubag.clients import LMSClient

T = TypeVar("T")


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


# Values of the settings form's "Submission Type" radio.
SUBMISSION_TYPE_LABELS = {"image": "Problem Set (variable length)", "pdf": "Exam/Quiz (templated, fixed length)"}

REGION_WARNING = (
    "its question regions may no longer line up with the new PDF; "
    "check them on the Edit Outline page"
)


def template_has_regions(submission_type: str | None) -> bool:
    """Whether an assignment's outline has question regions drawn on its template.

    Variable-length assignments ("image", Gradescope's Problem Set) have none.
    Templated ones ("pdf", Exam/Quiz) do, and so does anything unrecognized, to
    be safe.
    """
    return submission_type != "image"


def find_assignment_id(table_data: list[dict], name: str) -> str:
    """Find an assignment's ID by title in an Assignments page's ``table_data``.

    Titles are compared ignoring case and surrounding or repeated whitespace.

    Raises:
        LookupError: If no assignment, or more than one, has that title.
    """

    def normalize(title: str) -> str:
        return " ".join(str(title).split()).casefold()

    assignments = [e for e in table_data if e.get("type") == "assignment" and e.get("url")]
    matches = [e for e in assignments if normalize(e.get("title", "")) == normalize(name)]
    if not matches:
        titles = ", ".join(f"'{e.get('title')}'" for e in assignments) or "none"
        raise LookupError(f"No assignment titled '{name}'. Assignments in this course: {titles}")
    if len(matches) > 1:
        ids = ", ".join(e["url"].rstrip("/").rsplit("/", 1)[-1] for e in matches)
        raise LookupError(f"More than one assignment is titled '{name}' (IDs {ids}); use an ID instead")
    return matches[0]["url"].rstrip("/").rsplit("/", 1)[-1]


def validate_pdf(pdf_path: Path) -> Path:
    """Check that a path is an existing PDF file."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a .pdf file: {pdf_path}")
    with pdf_path.open("rb") as f:
        if f.read(4) != b"%PDF":
            raise ValueError(f"File does not look like a PDF: {pdf_path}")
    return pdf_path


class GradescopeClient(LMSClient):
    """Client to interact with the Gradescope platform.

    This client provides automated browser-based interactions with Gradescope
    for managing rosters, downloading assignments, syncing with LMS, and other
    course management tasks.

    Note on headless parameter:
        Methods that accept `headless` parameter default to:
        - `False` for `authenticate()` - interactive login may require manual steps
        - `True` for other operations - automated operations benefit from headless mode
    """

    base_url = "https://gradescope.com"

    @staticmethod
    def _default_auth_state_path() -> Path:
        """Get the platform-appropriate default path for the auth state file."""
        cache_dir = Path(platformdirs.user_cache_dir("edubag", "NYU"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "gradescope_auth.json"

    def __init__(self, base_url: str | None = None, auth_state_path: Path | None = None):
        """Initializes the GradescopeClient."""
        if base_url is not None:
            self.base_url = base_url
        if auth_state_path is not None:
            self.auth_state_path = auth_state_path
        else:
            self.auth_state_path = self._default_auth_state_path()

    def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        headless: bool = False,
    ) -> None:
        """Log into Gradescope and save the authentication state.

        Args:
            username (str | None): NetID to log in with. If None, user must enter manually in browser.
            password (str | None): Password for login. If None, user must enter manually in browser.
            headless (bool): Whether to run the browser in headless mode. Headless mode requires username and password.

        Raises:
            RuntimeError: If authentication fails.
        """
        # Load environment variables from .env file
        load_dotenv()

        # Try to get username and password from environment if not provided
        if username is None:
            username = os.getenv("GRADESCOPE_USERNAME")
        if password is None:
            password = os.getenv("GRADESCOPE_PASSWORD")

        # If username or password are not specified, browser must not be headless
        if username is None or password is None:
            headless = False

        def _do_authenticate() -> None:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context()
                page = context.new_page()

                page.goto(self.base_url)

                # Wait for page to load
                page.wait_for_load_state("domcontentloaded", timeout=10000)

                # Click the "Log In" button
                page.get_by_role("button", name="Log In").click()

                # Wait for login form to appear
                page.get_by_role("textbox", name="Email").wait_for(state="visible", timeout=10000)

                if username is not None:
                    page.get_by_role("textbox", name="Email").fill(username)
                    if password is not None:
                        page.get_by_role("textbox", name="Password").fill(password)
                        page.locator("#session_remember_me_label").click()
                        page.get_by_role("button", name="Log In").click()
                    else:
                        page.get_by_role("textbox", name="Email").click()
                        print("Please enter your password in the browser window.")
                else:
                    page.get_by_role("textbox", name="Password").click()
                    print("Please enter your username and password in the browser window.")

                # Wait for successful login (redirect to dashboard or account page)
                page.wait_for_url("**/account", timeout=60000)

                context.storage_state(path=self.auth_state_path)
                logger.debug(f"Authentication state saved at {self.auth_state_path}")

                browser.close()

        _run_sync_in_thread(_do_authenticate)

    def sync_roster(self, course: str, notify: bool = True, headless: bool = True) -> None:
        """Synchronize the course roster with the linked LMS.

        Args:
            course: Gradescope course ID or URL to the course home page
            notify: notify added users
            headless: Whether to run the browser in headless mode

        Raises:
            RuntimeError: If sync fails or authentication session expired.
        """
        def _do_sync_roster() -> None:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()

                # Navigate to the course page
                # Determine if course is a full URL or just an ID
                if course.startswith("http://") or course.startswith("https://"):
                    course_url = course
                else:
                    course_url = f"{self.base_url}/courses/{course}"
                page.goto(course_url)

                # Check if we need to re-login
                if "login" in page.url:
                    browser.close()
                    raise RuntimeError("Authentication session expired. Please re-authenticate.")

                try:
                    # Navigate to Roster page
                    page.get_by_role("link", name="Roster").click()
                    page.wait_for_load_state("networkidle")

                    # Try to click "More" button if it exists
                    more_button = page.locator(".js-toggleActionBarCollapsedMenu")
                    if more_button.count() > 0:
                        more_button.click()
                        page.wait_for_load_state("networkidle")

                    # Click the Sync button (using inexact match on "Sync")
                    # It has class js-openSyncLTIv1p3RosterModal
                    page.get_by_role("button", name="Sync", exact=False).first.click()
                    page.wait_for_load_state("networkidle")

                    # Handle the notification checkbox
                    sync_dialog = page.get_by_label("Sync with NYU Brightspace")
                    notify_checkbox = sync_dialog.get_by_text("Let new users know that they")

                    # Check the current state and update if needed
                    is_checked = notify_checkbox.is_checked()
                    if notify != is_checked:
                        notify_checkbox.click()

                    # Click the "Sync Roster" button
                    page.get_by_role("button", name="Sync Roster").click()

                    # Wait until the dialog disappears
                    page.get_by_role("button", name="Sync Roster").wait_for(state="detached", timeout=60000)

                    # Check for flash message alert
                    flash_alert = page.locator(".alert.alert-flashMessage.alert-success span").first
                    if flash_alert.count() > 0:
                        message = flash_alert.text_content()
                        logger.info(message)
                    else:
                        logger.info("Roster sync succeeded with no changes.")

                    browser.close()

                except Exception as e:
                    browser.close()
                    raise RuntimeError(f"Error during roster sync: {e}") from e

        _run_sync_in_thread(_do_sync_roster)

    def _save_roster_session(
        self,
        course: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> Path:
        """Internal method to save roster in a single browser session.

        Raises RuntimeError if authentication has expired.
        """
        def _do() -> Path:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path, accept_downloads=True)
                page = context.new_page()

                # Navigate to the course page
                # Determine if course is a full URL or just an ID
                if course.startswith("http://") or course.startswith("https://"):
                    course_url = course
                else:
                    course_url = f"{self.base_url}/courses/{course}"

                # Navigate to the memberships (roster) page
                roster_url = course_url.rstrip("/") + "/memberships"
                page.goto(roster_url)

                # Check if we need to re-login
                if "login" in page.url:
                    logger.error("Authentication session expired. Please re-authenticate.")
                    browser.close()
                    raise RuntimeError("Authentication session expired.")

                # Wait for page to load
                page.wait_for_load_state("domcontentloaded", timeout=10000)

                # Find the download roster link
                # It's an <a> element with href ending with "memberships.csv"
                download_link = page.locator('a[href$="/memberships.csv"]').first

                # Wait for the download link to be attached to the DOM
                download_link.wait_for(state="attached", timeout=10000)

                # Check if the download link is visible
                if not download_link.is_visible():
                    logger.debug("Download link is hidden, clicking 'More' button first")
                    # Click the "More" button to reveal hidden actions
                    more_button = page.locator(".js-toggleActionBarCollapsedMenu")
                    if more_button.count() > 0:
                        more_button.click()
                        # Give the UI a moment to update
                        time.sleep(0.5)
                    else:
                        logger.warning("'More' button not found")

                # Set up download expectation and click the link using JavaScript
                # This bypasses all visibility checks
                with page.expect_download() as download_info:
                    download_link.evaluate("element => element.click()")
                download = download_info.value

                # Save the download
                if save_dir is not None:
                    save_dir.mkdir(parents=True, exist_ok=True)
                    download_file_path = save_dir / download.suggested_filename
                else:
                    download_file_path = Path(download.suggested_filename)

                logger.info(f"Downloading roster to {download_file_path}")
                download.save_as(download_file_path)

                browser.close()
                return download_file_path

        return _run_sync_in_thread(_do)

    def save_roster(
        self,
        course: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Fetch and save the roster for a class on Gradescope.

        Args:
          * course: Gradescope course ID or URL to the course home page
          * save_dir: target directory of the saved roster file (default: current working directory)
          * headless (bool): Whether to run the browser in headless mode.

        Returns:
            list[Path]: list containing path to the saved roster file
            
        Raises:
            RuntimeError: If roster download fails or authentication expired.
        """
        # Ensure authentication state exists; trigger a login flow if missing
        if not self.auth_state_path.exists():
            logger.warning(f"Auth state file not found at {self.auth_state_path}. Running authentication...")
            self.authenticate(headless=headless)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                result_path = self._save_roster_session(course, save_dir, headless)
                return [result_path]
            except RuntimeError as e:
                if attempt < max_retries:
                    logger.warning(f"RuntimeError: {e} Authentication may have expired.")
                    logger.info("Re-authenticating...")
                    if self.auth_state_path.exists():
                        self.auth_state_path.unlink()
                    self.authenticate(headless=headless)
                    continue
                else:
                    logger.error(f"Max retries exceeded. RuntimeError: {e}")
                    raise
        return []

    def _extract_course_details(self, page: Page) -> dict:
        """Extract course details from a Gradescope course page.

        Args:
            page: The Playwright page object for the course home page.

        Returns:
            Dictionary with course detail information.
        """
        course_details = {}

        # Extract course number from the h1.courseHeader--title
        course_number_element = page.locator("h1.courseHeader--title")
        if course_number_element.count() > 0:
            text = course_number_element.text_content()
            if text:
                course_details["course_number"] = text.strip()

        # Extract course name from the sidebar subtitle (format: "MATH-UA 122.006 Calculus II, Spring 2026")
        sidebar_subtitle = page.locator("div.sidebar--subtitle")
        if sidebar_subtitle.count() > 0:
            subtitle_text = sidebar_subtitle.text_content()
            if subtitle_text:
                course_details["course_name"] = subtitle_text.strip()

        # Extract Course ID from div.courseHeader--courseID
        course_id_element = page.locator("div.courseHeader--courseID")
        if course_id_element.count() > 0:
            course_id_text = course_id_element.text_content()
            if course_id_text:
                course_id_text = course_id_text.strip()
                # Extract just the number from "Course ID: 1227665"
                course_id_match = re.search(r"(\d+)", course_id_text)
                if course_id_match:
                    course_details["course_id"] = course_id_match.group(1)

        # Extract instructors from the sidebar roster (aria-label="Instructor: ...")
        instructor_items = page.locator("li[aria-label^='Instructor:']")
        if instructor_items.count() > 0:
            instructors = []
            for item in instructor_items.all():
                aria_label = item.get_attribute("aria-label")
                # Extract name from "Instructor: Name" format
                if aria_label and aria_label.startswith("Instructor:"):
                    name = aria_label.replace("Instructor:", "").strip()
                    instructors.append(name)
            if instructors:
                course_details["instructors"] = instructors

        # Navigate to the course edit page to extract LMS resource information
        edit_url = page.url.rstrip("/") + "/edit"
        page.goto(edit_url)
        page.wait_for_load_state("domcontentloaded", timeout=10000)

        # Extract LMS resource information from the edit page
        lms_resource = page.locator("div.lmsResource[data-lms-id]")
        if lms_resource.count() > 0:
            lms_id = lms_resource.get_attribute("data-lms-id")
            if lms_id:
                course_details["lms_course_id"] = lms_id
            
            lms_text = lms_resource.text_content()
            if lms_text and "Linked to:" in lms_text:
                lms_name = lms_text.split("Linked to:", 1)[1].strip()
                course_details["lms_course_name"] = lms_name

        return course_details

    def _fetch_class_details_session(
        self,
        course_name: str,
        term: str | Term,
        headless: bool = True,
    ) -> list[dict]:
        """Internal method to fetch class details in a single browser session.

        Raises RuntimeError if authentication has expired.
        """
        def _do() -> list[dict]:
            result = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()

                page.goto(self.base_url)
                if "login" in page.url:
                    browser.close()
                    raise RuntimeError("Authentication session expired.")

                # Wait for the course list to load
                page.wait_for_load_state("networkidle")

                # Convert term to string representation (e.g., "FALL 2025")
                term_str = str(term)

                # Find all term divs and filter for the exact one we want
                term_divs = page.locator("div.courseList--term")
                term_count = term_divs.count()
                matching_term_index = -1

                # Find the term div that contains our term string
                for i in range(term_count):
                    term_div = term_divs.nth(i)
                    term_text = term_div.text_content()
                    if term_text and term_str in term_text:
                        matching_term_index = i
                        break

                if matching_term_index == -1:
                    logger.warning(f"Term '{term_str}' not found on page")
                    browser.close()
                    return result

                # Get all coursesForTerm containers and find the one after our matching term
                courses_for_term_divs = page.locator("div.courseList--coursesForTerm")
                courses_for_term_count = courses_for_term_divs.count()

                # The courses container should be at the same index as the term (terms and containers alternate)
                if matching_term_index < courses_for_term_count:
                    courses_container = courses_for_term_divs.nth(matching_term_index)
                else:
                    logger.warning(
                        f"Courses container index {matching_term_index} out of range (only {courses_for_term_count} containers)"
                    )
                    browser.close()
                    return result

                if courses_container.count() == 0:
                    logger.warning(f"No courses found for term '{term_str}'")
                    browser.close()
                    return result

                # Normalize whitespace in course name (handle line breaks, multiple spaces, etc.)
                normalized_course_name = re.sub(r"\s+", " ", course_name).strip()

                # Build a regex once for reuse with locator filters
                course_regex = re.compile(re.escape(normalized_course_name), re.IGNORECASE)
                logger.debug(f"Looking for course matching regex: {course_regex.pattern}")

                # Locate matching course boxes via Playwright locator filters
                course_boxes = courses_container.locator("a.courseBox")
                by_name = course_boxes.filter(has=page.locator("div.courseBox--name", has_text=course_regex))
                # Fallback: match on any text inside the course box
                by_box_text = course_boxes.filter(has_text=course_regex)

                # Combine matches using Playwright's locator union
                matching_courses = by_name.or_(by_box_text).all()
                logger.debug(f"Found {len(matching_courses)} matching course boxes")

                # Now visit each matching course and extract details
                for course_link in matching_courses:
                    course_url = course_link.get_attribute("href")
                    if course_url:
                        # Validate and construct the full URL safely
                        # Ensure course_url is a relative path starting with /
                        if not course_url.startswith("/"):
                            logger.warning(f"Skipping invalid course URL: {course_url}")
                            continue

                        # Navigate to the course page
                        full_url = f"{self.base_url}{course_url}"
                        page.goto(full_url)
                        page.wait_for_load_state("networkidle")

                        # Extract course details
                        course_details = self._extract_course_details(page)
                        result.append(course_details)
                        logger.info(f"Extracted details for course: {course_details.get('course_name', 'Unknown')}")

                        # Go back to the home page for the next iteration
                        page.goto(self.base_url)
                        page.wait_for_load_state("networkidle")

                browser.close()
                return result

        return _run_sync_in_thread(_do)

    def fetch_class_details(
        self,
        course_name: str,
        term: str | Term,
        username: str | None = None,
        password: str | None = None,
        headless: bool = True,
        output: Path | None = None,
    ) -> list[dict]:
        """Fetch class details for a course offering and optionally save.

        Args:
          * course_name (str): The name of the course.
          * term (str | Term): The term of the course.
          * username (str | None): NetID to log in with. If None, user must enter manually.
          * password (str | None): Password for login. If None, user must enter manually.
          * headless (bool): Whether to run the browser in headless mode.
          * output (Path | None): Path to save the output JSON. If None, doesn't save.

        Returns:
            list[dict]: List of dictionaries with class details.
        """
        # Check if authentication state exists; if not, authenticate first
        if not self.auth_state_path.exists():
            logger.warning(f"Auth state file not found at {self.auth_state_path}. Running authentication...")
            self.authenticate(username=username, password=password, headless=headless)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                result = self._fetch_class_details_session(course_name, term, headless)

                # Save to output if specified
                if output is not None:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with output.open("w") as f:
                        json.dump(result, f, indent=2)
                    logger.info(f"Class details saved to {output}")

                return result
            except RuntimeError as e:
                if attempt < max_retries:
                    logger.warning(f"RuntimeError: {e} Authentication may have expired.")
                    logger.info("Re-authenticating...")
                    if self.auth_state_path.exists():
                        self.auth_state_path.unlink()
                    self.authenticate(username=username, password=password, headless=headless)
                else:
                    logger.error(f"Max retries exceeded. RuntimeError: {e}")
                    raise
        return []

    def _save_download_session(
        self,
        url: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> Path:
        """Internal: navigate to a download URL in a single browser session and save the file.

        Args:
            url: Fully-qualified URL that responds with a file download.
            save_dir: Directory to save the file into. Defaults to the current directory.
            headless: Whether to run the browser headless.

        Returns:
            Path to the saved file.

        Raises:
            RuntimeError: If authentication has expired or the download fails.
        """
        def _do() -> Path:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(
                    storage_state=self.auth_state_path, accept_downloads=True
                )
                page = context.new_page()

                # Verify auth by doing a lightweight pre-check on the base URL
                page.goto(self.base_url)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                if "login" in page.url:
                    browser.close()
                    raise RuntimeError("Authentication session expired. Please re-authenticate.")

                try:
                    return self._download_in_page(page, url, save_dir)
                finally:
                    browser.close()

        return _run_sync_in_thread(_do)

    @staticmethod
    def _download_in_page(
        page: Page, url: str, save_dir: Path | None = None, filename: str | None = None
    ) -> Path:
        """Navigate an authenticated page to a download URL and save the file.

        The file is saved as ``filename``, or by default under the name the server suggests.

        Raises:
            RuntimeError: If authentication has expired or no download starts.
        """
        try:
            with page.expect_download(timeout=60000) as download_info:
                # Direct navigation to file endpoints can throw browser-level
                # navigation errors even when the download succeeds.
                try:
                    page.goto(url)
                except PlaywrightError as exc:
                    msg = str(exc)
                    if "Download is starting" not in msg and "ERR_ABORTED" not in msg:
                        raise
            download = download_info.value
        except PlaywrightTimeoutError as exc:
            # No download event was observed. If we were redirected, auth likely expired.
            if "login" in page.url:
                raise RuntimeError("Authentication session expired. Please re-authenticate.") from exc
            raise RuntimeError(f"Timed out waiting for download event at {url}") from exc

        filename = filename or download.suggested_filename
        if save_dir is not None:
            save_dir.mkdir(parents=True, exist_ok=True)
            download_file_path = save_dir / filename
        else:
            download_file_path = Path(filename)

        logger.info(f"Saving {download.suggested_filename} to {download_file_path}")
        download.save_as(download_file_path)
        return download_file_path

    def _download_with_retry(
        self,
        url: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Download a URL, re-authenticating once on session expiry.

        Args:
            url: Fully-qualified download URL.
            save_dir: Directory to save the file into.
            headless: Whether to run the browser headless.

        Returns:
            List containing the path to the saved file.
        """
        if not self.auth_state_path.exists():
            logger.warning(
                f"Auth state file not found at {self.auth_state_path}. Running authentication..."
            )
            self.authenticate(headless=headless)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                result_path = self._save_download_session(url, save_dir, headless)
                return [result_path]
            except RuntimeError as e:
                if attempt < max_retries:
                    if "Authentication session expired" in str(e):
                        logger.warning(f"RuntimeError: {e} Re-authenticating...")
                        if self.auth_state_path.exists():
                            self.auth_state_path.unlink()
                        self.authenticate(headless=headless)
                        continue
                    raise
                else:
                    logger.error(f"Max retries exceeded. RuntimeError: {e}")
                    raise
        return []

    def save_assignment_scores(
        self,
        course: str,
        assignment: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Download the scores CSV for a Gradescope assignment.

        Args:
            course: Gradescope course ID.
            assignment: Gradescope assignment ID.
            save_dir: Directory to save the file into. Defaults to the current directory.
            headless: Whether to run the browser in headless mode.

        Returns:
            list[Path]: List containing the path to the saved scores CSV file.

        Raises:
            RuntimeError: If the download fails or authentication expired.
        """
        url = f"{self.base_url}/courses/{course}/assignments/{assignment}/scores.csv"
        logger.debug(f"Downloading assignment scores from {url}")
        return self._download_with_retry(url, save_dir, headless)

    def save_assignment_container_scores(
        self,
        course: str,
        assignment_container: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Download the versioned scores zip for a Gradescope assignment container.

        Args:
            course: Gradescope course ID.
            assignment_container: Gradescope assignment container ID.
            save_dir: Directory to save the file into. Defaults to the current directory.
            headless: Whether to run the browser in headless mode.

        Returns:
            list[Path]: List containing the path to the saved zip file.

        Raises:
            RuntimeError: If the download fails or authentication expired.
        """
        url = (
            f"{self.base_url}/courses/{course}/assignment_containers/"
            f"{assignment_container}/scores/csv.zip"
        )
        logger.debug(f"Downloading assignment container scores from {url}")
        return self._download_with_retry(url, save_dir, headless)

    def _with_reauth(
        self,
        session: Callable[[], T],
        headless: bool,
        username: str | None = None,
        password: str | None = None,
    ) -> T:
        """Run a browser session, re-authenticating once if the saved session has expired."""
        if not self.auth_state_path.exists():
            logger.warning(
                f"Auth state file not found at {self.auth_state_path}. Running authentication..."
            )
            self.authenticate(username=username, password=password, headless=headless)
        try:
            return session()
        except RuntimeError as e:
            if "Authentication session expired" not in str(e):
                raise
            logger.warning(f"RuntimeError: {e} Re-authenticating...")
            if self.auth_state_path.exists():
                self.auth_state_path.unlink()
            self.authenticate(username=username, password=password, headless=headless)
            return session()

    @staticmethod
    def container_versions(table_data: list[dict], container: str) -> list[dict]:
        """The version assignments of an assignment container, from Assignments ``table_data``.

        Returns:
            list[dict]: One entry per version, in Gradescope's order, with ``id`` (assignment
                ID), ``title``, ``version_name``, ``num_active_submissions`` and
                ``grading_progress``.
        """
        versions = [
            e
            for e in table_data
            if e.get("type") == "assignment" and str(e.get("container_id")) == str(container)
        ]
        versions.sort(key=lambda e: (e.get("version_index") is None, e.get("version_index")))
        return [
            {
                "id": e["url"].rstrip("/").rsplit("/", 1)[-1],
                "title": e.get("title"),
                "version_name": e.get("version_name"),
                "num_active_submissions": e.get("num_active_submissions"),
                "grading_progress": e.get("grading_progress"),
            }
            for e in versions
        ]

    def list_container_versions(
        self, course: str, container: str, headless: bool = True
    ) -> list[dict]:
        """List the version assignments of an assignment container (version set).

        See ``container_versions`` for the fields of each entry.
        """
        return self.container_versions(self.list_assignments(course, headless), container)

    def save_grading_snapshot(
        self,
        course: str,
        containers: list[str],
        save_dir: Path,
        scores: bool = True,
        headless: bool = True,
        username: str | None = None,
        password: str | None = None,
    ) -> Path:
        """Save a snapshot of the grading of one or more assignment containers (version sets).

        In a single browser session: finds each container's versions (the version links on its
        Review Grades page, which unlike the Assignments table also lists bubble-sheet
        versions), reads each version's Grading Dashboard (per-question graded/total counts
        and graders), and, if ``scores``, downloads each version's scores CSV as
        ``<assignment id>_scores.csv``.

        Writes ``save_dir/dashboards.json``::

            {"course": ..., "fetched_at": ISO time,
             "containers": [{"id": ..., "title": ...,
                             "versions": [{"id", "title", "version_name",
                                           "num_active_submissions", "grading_progress",
                                           "scores_csv": file name or null,
                                           "dashboard": GradingDashboard assignment props}]}]}

        ``version_name`` is the version's link text on the container page (e.g. "Version A");
        ``title``, ``num_active_submissions`` and ``grading_progress`` come from the
        Assignments table and are null for versions it does not list.

        ``username``/``password`` are used to log in again if the saved session has expired.

        Returns:
            Path to ``dashboards.json``.

        Raises:
            RuntimeError: If authentication has expired, or a container or dashboard is not found.
        """
        save_dir = Path(save_dir)

        def expired(page: Page) -> None:
            if "login" in page.url:
                raise RuntimeError("Authentication session expired. Please re-authenticate.")

        def _do() -> Path:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(
                        storage_state=self.auth_state_path, accept_downloads=True
                    )
                    page = context.new_page()
                    page.goto(f"{self._course_url(course)}/assignments")
                    page.wait_for_load_state("networkidle")
                    expired(page)
                    table_data = self._read_assignments_table(page)
                    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")

                    snapshot: dict = {"course": str(course), "fetched_at": fetched_at, "containers": []}
                    for container in containers:
                        entry = next(
                            (
                                e
                                for e in table_data
                                if e.get("type") == "assignment_container"
                                and str(e.get("id")).rsplit("_", 1)[-1] == str(container)
                            ),
                            None,
                        )
                        if entry is None:
                            raise RuntimeError(f"No assignment container {container} in course {course}")
                        listed = {v["id"]: v for v in self.container_versions(table_data, container)}

                        page.goto(f"{self._course_url(course)}/assignment_containers/{container}/review_grades")
                        page.wait_for_load_state("networkidle")
                        expired(page)
                        versions = []
                        for link in page.locator('a[href$="/review_grades"]').all():
                            match = re.search(r"/assignments/(\d+)/review_grades$", link.get_attribute("href") or "")
                            if match is None or any(v["id"] == match[1] for v in versions):
                                continue
                            version = {
                                "id": match[1],
                                "title": None,
                                "num_active_submissions": None,
                                "grading_progress": None,
                                **listed.get(match[1], {}),
                            }
                            version["version_name"] = " ".join(link.inner_text().split()) or version.get(
                                "version_name"
                            )
                            versions.append(version)
                        if not versions:
                            raise RuntimeError(f"No versions found for assignment container {container}")

                        for version in versions:
                            page.goto(f"{self._assignment_url(course, version['id'])}/grade")
                            page.wait_for_load_state("networkidle")
                            expired(page)
                            dashboard = page.locator('[data-react-class="GradingDashboard"]')
                            if dashboard.count() == 0:
                                raise RuntimeError(f"No Grading Dashboard at {page.url}")
                            props = json.loads(dashboard.get_attribute("data-react-props") or "{}")
                            version["dashboard"] = props.get("presenter", {}).get("assignments", {}).get(version["id"])
                            if version["dashboard"] is None:
                                raise RuntimeError(f"Grading Dashboard at {page.url} has no data")
                            version["scores_csv"] = None
                            if scores:
                                version["scores_csv"] = self._download_in_page(
                                    page,
                                    f"{self._assignment_url(course, version['id'])}/scores.csv",
                                    save_dir,
                                    filename=f"{version['id']}_scores.csv",
                                ).name
                        snapshot["containers"].append(
                            {"id": str(container), "title": entry.get("title"), "versions": versions}
                        )
                finally:
                    browser.close()

            save_dir.mkdir(parents=True, exist_ok=True)
            path = save_dir / "dashboards.json"
            path.write_text(json.dumps(snapshot, indent=2))
            logger.info(f"Saved grading snapshot of course {course} to {path}")
            return path

        return self._with_reauth(lambda: _run_sync_in_thread(_do), headless, username, password)

    def send_roster(
        self,
        course: str,
        csv_path: Path,
        notify: bool = False,
        role: str = "Student",
        headless: bool = True,
    ) -> None:
        """Upload users from a csv file to a course on Gradescope.

        Users are added or updated based on the contents of the CSV file.
        For example, the file might include additional staff members to add to the course.
        Or it might contain section information to update existing students.

        Args:
          * course: Gradescope course ID or URL to the course home page
          * csv_path: path to the roster CSV file to upload
          * notify (bool): Whether to notify users by email of being added. Default False.
          * role (str): Role to add users as. Must be one of "Student", "Instructor", "TA", "Reader". Default "Student".
          * headless (bool): Whether to run the browser in headless mode.

        Returns:
            None

        Raises:
            RuntimeError: If roster upload fails or authentication expired.
        """
        if not csv_path.exists():
            raise FileNotFoundError(f"Roster CSV not found: {csv_path}")

        # Validate role parameter
        valid_roles = {"Student", "Instructor", "TA", "Reader"}
        if role not in valid_roles:
            raise ValueError(f"Invalid role '{role}'. Must be one of {valid_roles}")

        # Ensure authentication state exists; trigger a login flow if missing
        if not self.auth_state_path.exists():
            logger.warning(
                f"Auth state file not found at {self.auth_state_path}. Running authentication..."
            )
            self.authenticate(headless=headless)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                self._send_roster_session(course, csv_path, notify=notify, role=role, headless=headless)
                return
            except RuntimeError as e:
                if attempt < max_retries:
                    logger.warning(f"RuntimeError: {e} Authentication may have expired.")
                    logger.info("Re-authenticating...")
                    if self.auth_state_path.exists():
                        self.auth_state_path.unlink()
                    self.authenticate(headless=headless)
                    continue
                else:
                    logger.error(f"Max retries exceeded. RuntimeError: {e}")
                    raise

    def _send_roster_session(
        self,
        course: str,
        csv_path: Path,
        notify: bool = False,
        role: str = "Student",
        headless: bool = True,
    ) -> None:
        """Internal method to upload a roster in a single browser session.

        Args:
            course: Gradescope course ID or URL to the course home page
            csv_path: Path to the roster CSV file to upload
            notify (bool): Whether to notify users by email. Default False.
            role (str): Role to add users as. Must be one of "Student", "Instructor", "TA", "Reader". Default "Student".
            headless (bool): Whether to run the browser in headless mode.

        Raises RuntimeError if authentication has expired or upload fails.
        """
        # Map role names to radio button values
        role_to_value = {
            "Student": "0",
            "Instructor": "1",
            "TA": "2",
            "Reader": "3",
        }
        role_value = role_to_value.get(role)
        if role_value is None:
            raise ValueError(f"Invalid role '{role}'. Must be one of {list(role_to_value.keys())}")

        def _do() -> None:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()

                # Navigate to the course page
                if course.startswith("http://") or course.startswith("https://"):
                    course_url = course
                else:
                    course_url = f"{self.base_url}/courses/{course}"
                page.goto(course_url)

                # Check if we need to re-login
                if "login" in page.url:
                    browser.close()
                    raise RuntimeError("Authentication session expired. Please re-authenticate.")

                try:
                    # Navigate directly to memberships (roster) page
                    roster_url = course_url.rstrip("/") + "/memberships"
                    page.goto(roster_url)
                    page.wait_for_load_state("networkidle")

                    # Open the Add Students or Staff dialog
                    page.get_by_role(
                        "button", name="Add Students or Staff", exact=False
                    ).click()

                    # Select CSV File option
                    page.get_by_role("button", name="CSV File", exact=False).click()

                    # Trigger file chooser and upload
                    page.get_by_role("button", name="Select CSV", exact=False).click()
                    page.get_by_label("File *Please select a").set_input_files(csv_path)

                    # Scope selectors to the visible dialog to avoid duplicate IDs
                    dialog = page.locator("dialog").filter(has_text="Import course members")

                    # Handle notify checkbox using JavaScript (form elements may not be "visible" due to styling)
                    notify_checkbox = dialog.locator("#notify_by_email")
                    if notify_checkbox.count() > 0:
                        # Use JavaScript to set the checkbox state directly
                        current_checked = notify_checkbox.is_checked()
                        if notify != current_checked:
                            # Toggle using JavaScript to bypass visibility requirements
                            notify_checkbox.evaluate("el => el.click()")
                        logger.debug(f"Notify checkbox set to: {notify}")

                    # Handle role radio button selection using JavaScript
                    role_radio = dialog.locator(f"input[name='options[role]'][value='{role_value}']")
                    if role_radio.count() > 0:
                        # Use JavaScript to click the radio button directly
                        role_radio.evaluate("el => el.click()")
                        logger.debug(f"Role set to: {role}")

                    # Step through import flow
                    page.get_by_role("button", name="Next", exact=False).click()
                    page.get_by_role("button", name="Import", exact=False).click()

                    # Wait for upload to complete (flash message or dialog close)
                    page.wait_for_load_state("networkidle")

                    # Extract and log all flash messages
                    flash_messages = page.locator(".alert.alert-flashMessage")
                    if flash_messages.count() > 0:
                        for i in range(flash_messages.count()):
                            flash = flash_messages.nth(i)
                            message_text = flash.locator("span").first.text_content()
                            if message_text:
                                message_text = message_text.strip()
                                # Determine message type based on alert class
                                alert_class = flash.get_attribute("class") or ""
                                if "alert-success" in alert_class:
                                    logger.success(message_text)
                                elif "alert-warning" in alert_class:
                                    logger.warning(message_text)
                                elif "alert-error" in alert_class:
                                    logger.error(message_text)
                                else:
                                    logger.info(message_text)
                    else:
                        logger.info("Roster upload submitted.")

                    browser.close()
                except Exception as e:
                    browser.close()
                    raise RuntimeError(f"Error during roster upload: {e}") from e

        _run_sync_in_thread(_do)

    def list_assignments(self, course: str, headless: bool = True) -> list[dict]:
        """Fetch the assignment/quiz-container list from a course's Assignments page.

        Args:
            course: Gradescope course ID or URL to the course home page.
            headless: Whether to run the browser in headless mode.

        Returns:
            list[dict]: Raw ``table_data`` entries from the Assignments page, mixing
                ``type: "assignment"`` and ``type: "assignment_container"`` records.
                Each entry has a ``url`` field (relative path) usable for further requests.

        Raises:
            RuntimeError: If authentication has expired.
        """
        if not self.auth_state_path.exists():
            logger.warning(
                f"Auth state file not found at {self.auth_state_path}. Running authentication..."
            )
            self.authenticate(headless=headless)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                return self._list_assignments_session(course, headless)
            except RuntimeError as e:
                if attempt < max_retries:
                    logger.warning(f"RuntimeError: {e} Re-authenticating...")
                    if self.auth_state_path.exists():
                        self.auth_state_path.unlink()
                    self.authenticate(headless=headless)
                    continue
                logger.error(f"Max retries exceeded. RuntimeError: {e}")
                raise
        return []

    def _list_assignments_session(self, course: str, headless: bool) -> list[dict]:
        """Internal: fetch the Assignments page's embedded table data in one browser session.

        Raises RuntimeError if authentication has expired.
        """
        def _do() -> list[dict]:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()

                if course.startswith("http://") or course.startswith("https://"):
                    course_url = course.rstrip("/")
                else:
                    course_url = f"{self.base_url}/courses/{course}"
                page.goto(f"{course_url}/assignments")
                page.wait_for_load_state("networkidle")

                if "login" in page.url:
                    browser.close()
                    raise RuntimeError("Authentication session expired. Please re-authenticate.")

                # The Assignments page renders its full table as one embedded JSON blob.
                table_data = self._read_assignments_table(page)

                browser.close()
                return table_data

        return _run_sync_in_thread(_do)

    def post_all_grades(self, course: str, headless: bool = True) -> list[dict]:
        """Post grades to the linked LMS for every eligible assignment in a course.

        For each standalone assignment or quiz/exam version-container (individual quiz
        versions are skipped; their container posts grades for all versions at once),
        visits its Review Grades page and, if grades are published there and an LMS
        resource link is present, POSTs to the same endpoint the "Post Grades" button
        uses.

        Args:
            course: Gradescope course ID or URL to the course home page.
            headless: Whether to run the browser in headless mode.

        Returns:
            list[dict]: One result dict per assignment/container attempted, each with
                ``id``, ``title``, ``type``, ``url`` and ``status`` (one of
                ``"posted"``, ``"skipped-unpublished"``, ``"skipped-not-linked"``,
                ``"skipped-no-grades"``, ``"error"``), plus ``detail`` on error.

        Raises:
            RuntimeError: If authentication has expired.
        """
        if not self.auth_state_path.exists():
            logger.warning(
                f"Auth state file not found at {self.auth_state_path}. Running authentication..."
            )
            self.authenticate(headless=headless)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                return self._post_all_grades_session(course, headless)
            except RuntimeError as e:
                if attempt < max_retries:
                    logger.warning(f"RuntimeError: {e} Re-authenticating...")
                    if self.auth_state_path.exists():
                        self.auth_state_path.unlink()
                    self.authenticate(headless=headless)
                    continue
                logger.error(f"Max retries exceeded. RuntimeError: {e}")
                raise
        return []

    @staticmethod
    def _is_assignment_published(entry: dict, page: Page) -> bool:
        is_published = entry.get("is_published")
        if is_published is not None:
            return bool(is_published)

        return any(
            page.get_by_role("button", name=label).count() > 0
            for label in ("Unpublish Grades", "Unpublish All Grades")
        )

    def _post_all_grades_session(self, course: str, headless: bool) -> list[dict]:
        """Internal: enumerate and post grades for one course in a single browser session.

        Raises RuntimeError if authentication has expired or the Assignments page
        can't be loaded; per-assignment failures are captured in the returned results
        instead of raising, so one bad assignment doesn't abort the whole course.
        """
        def _do() -> list[dict]:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path)
                page = context.new_page()

                if course.startswith("http://") or course.startswith("https://"):
                    course_url = course.rstrip("/")
                else:
                    course_url = f"{self.base_url}/courses/{course}"
                page.goto(f"{course_url}/assignments")
                page.wait_for_load_state("networkidle")

                if "login" in page.url:
                    browser.close()
                    raise RuntimeError("Authentication session expired. Please re-authenticate.")

                table = page.locator('[data-react-class="AssignmentsTable"]')
                table_data = []
                if table.count() > 0:
                    props = json.loads(table.get_attribute("data-react-props") or "{}")
                    table_data = props.get("table_data", [])

                results: list[dict] = []
                for entry in table_data:
                    entry_type = entry.get("type")
                    entry_url = entry.get("url")
                    if entry_type != "assignment_container" and entry_type != "assignment":
                        continue
                    if entry_type == "assignment" and entry.get("container_id"):
                        # An individual quiz/exam version; its container posts for all versions.
                        continue
                    if not entry_url:
                        continue

                    result = {
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "type": entry_type,
                        "url": entry_url,
                    }

                    try:
                        review_url = f"{self.base_url}{entry_url}/review_grades"
                        page.goto(review_url)
                        page.wait_for_load_state("networkidle")

                        if "login" in page.url:
                            browser.close()
                            raise RuntimeError(
                                "Authentication session expired. Please re-authenticate."
                            )

                        if not self._is_assignment_published(entry, page):
                            result["status"] = "skipped-unpublished"
                            results.append(result)
                            continue

                        post_grades_el = page.locator('[data-react-class="PostGrades"]')
                        if post_grades_el.count() == 0:
                            result["status"] = "skipped-not-linked"
                            results.append(result)
                            continue

                        post_props = json.loads(
                            post_grades_el.get_attribute("data-react-props") or "{}"
                        )
                        post_grades_path = post_props.get("postGradesPath")
                        if not post_props.get("gradesPresent") or not post_grades_path:
                            result["status"] = "skipped-no-grades"
                            results.append(result)
                            continue

                        csrf_token = page.locator('meta[name="csrf-token"]').get_attribute(
                            "content"
                        )
                        # Resolve against page.url (not self.base_url): gradescope.com redirects
                        # to www.gradescope.com, and a POST across that redirect gets downgraded
                        # to a GET, 404-ing on this POST-only route.
                        post_url = urljoin(page.url, post_grades_path)
                        response = page.request.post(
                            post_url,
                            headers={
                                "X-CSRF-Token": csrf_token or "",
                                "Accept": "application/json",
                            },
                        )
                        if response.ok:
                            result["status"] = "posted"
                            result["lms_name"] = post_props.get("lmsName")
                            logger.success(
                                f"Posted grades for '{entry.get('title')}' to "
                                f"{post_props.get('lmsName')}"
                            )
                        else:
                            result["status"] = "error"
                            result["detail"] = f"HTTP {response.status}: {response.text()}"
                            logger.error(
                                f"Failed to post grades for '{entry.get('title')}': "
                                f"{result['detail']}"
                            )
                    except RuntimeError:
                        raise
                    except Exception as exc:
                        result["status"] = "error"
                        result["detail"] = str(exc)
                        logger.error(f"Error posting grades for '{entry.get('title')}': {exc}")

                    results.append(result)

                browser.close()
                return results

        return _run_sync_in_thread(_do)

    def _course_url(self, course: str) -> str:
        """URL of a course home page, given a course ID or course URL."""
        if course.startswith("http://") or course.startswith("https://"):
            return course.rstrip("/")
        return f"{self.base_url}/courses/{course}"

    def _assignment_url(self, course: str, assignment: str) -> str:
        """URL of an assignment, given a course ID or course URL."""
        return f"{self._course_url(course)}/assignments/{assignment}"

    @staticmethod
    def _read_assignments_table(page: Page) -> list[dict]:
        """The Assignments page's embedded table data (the page must be loaded)."""
        table = page.locator('[data-react-class="AssignmentsTable"]')
        if table.count() == 0:
            return []
        return json.loads(table.get_attribute("data-react-props") or "{}").get("table_data", [])

    def replace_template_pdf(
        self,
        course: str,
        assignment: str,
        pdf_path: Path,
        allow_region_shift: bool = False,
        dry_run: bool = False,
        headless: bool = True,
    ) -> dict:
        """Replace an assignment's template PDF and re-save its outline.

        This is the settings page's "Replace PDF" followed by "Save Assignment"
        and, on the outline page that follows, "Save Outline".

        A templated assignment (Exam/Quiz) has question regions drawn on its
        template, which a new PDF can leave misplaced; it is refused unless
        ``allow_region_shift`` is set. A variable-length assignment (Problem
        Set) has no regions.

        Args:
            course: Gradescope course ID or URL to the course home page.
            assignment: Gradescope assignment ID, or the assignment's title
                (matched ignoring case; it must be unique in the course).
            pdf_path: The new template PDF.
            allow_region_shift: Proceed even if the assignment has question regions.
            dry_run: Find the form controls and report, but submit nothing.
            headless: Whether to run the browser in headless mode.

        Returns:
            dict: ``course``, ``assignment``, ``title``, ``pdf``, ``type``,
                ``regions_need_review``, ``uploaded``, ``outline_saved``,
                ``outline_url`` and ``dry_run``.

        Raises:
            RuntimeError: If authentication has expired or a save fails.
            LookupError: If no assignment, or more than one, has the given title.
            PermissionError: If the assignment has question regions and
                ``allow_region_shift`` is not set.
        """
        pdf_path = validate_pdf(pdf_path)
        if not self.auth_state_path.exists():
            logger.warning(
                f"Auth state file not found at {self.auth_state_path}. Running authentication..."
            )
            self.authenticate(headless=headless)

        # Stages completed so far, so a retry never uploads twice.
        state: dict = {}
        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                return self._replace_template_pdf_session(
                    course, assignment, pdf_path, allow_region_shift, dry_run, headless, state
                )
            except RuntimeError as e:
                if attempt < max_retries and "Authentication session expired" in str(e):
                    logger.warning(f"RuntimeError: {e} Re-authenticating...")
                    if self.auth_state_path.exists():
                        self.auth_state_path.unlink()
                    self.authenticate(headless=headless)
                    continue
                raise
        raise AssertionError("unreachable")

    def _replace_template_pdf_session(
        self,
        course: str,
        assignment: str,
        pdf_path: Path,
        allow_region_shift: bool,
        dry_run: bool,
        headless: bool,
        state: dict,
    ) -> dict:
        """Internal: replace a template PDF in a single browser session.

        ``state`` carries the result and completed stages across a
        re-authentication retry. Raises RuntimeError if authentication has expired.
        """

        def _expired(page: Page) -> None:
            if "login" in page.url:
                raise RuntimeError("Authentication session expired. Please re-authenticate.")

        def _do() -> dict:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(storage_state=self.auth_state_path)
                    if dry_run:
                        # A dry run must not change anything.
                        context.route(
                            "**/*",
                            lambda route: route.continue_()
                            if route.request.method in ("GET", "HEAD")
                            else route.abort(),
                        )
                    page = context.new_page()

                    if "assignment_id" not in state:
                        if assignment.isdigit():
                            state["assignment_id"] = assignment
                        else:
                            # Look the title up on the Assignments page.
                            page.goto(f"{self._course_url(course)}/assignments")
                            page.wait_for_load_state("networkidle")
                            _expired(page)
                            state["assignment_id"] = find_assignment_id(
                                self._read_assignments_table(page), assignment
                            )
                    assignment_id = state["assignment_id"]
                    assignment_url = self._assignment_url(course, assignment_id)
                    outline_url = f"{assignment_url}/outline/edit"

                    if not state.get("uploaded"):
                        page.goto(f"{assignment_url}/edit")
                        page.wait_for_load_state("domcontentloaded")
                        _expired(page)
                        form = page.locator("form#assignment_form")
                        try:
                            form.wait_for(state="attached", timeout=30000)
                        except PlaywrightTimeoutError as exc:
                            raise RuntimeError(f"No assignment settings form at {assignment_url}/edit") from exc

                        checked = form.locator("input[name='assignment[submission_type]']:checked")
                        submission_type = checked.get_attribute("value") if checked.count() else None
                        needs_review = template_has_regions(submission_type)
                        # The title is an input, or fixed text when the assignment is linked to an LMS.
                        title_input = form.locator("#assignment_title")
                        locked_title = page.locator(".lockedAssignmentName--title")
                        if title_input.count():
                            title = title_input.input_value()
                        elif locked_title.count():
                            title = locked_title.first.inner_text().strip()
                        else:
                            title = f"assignment {assignment_id}"
                        state["result"] = {
                            "course": course,
                            "assignment": assignment_id,
                            "title": title,
                            "pdf": str(pdf_path),
                            "type": SUBMISSION_TYPE_LABELS.get(submission_type, "unknown"),
                            "regions_need_review": needs_review,
                            "uploaded": False,
                            "outline_saved": False,
                            "outline_url": outline_url,
                            "dry_run": dry_run,
                        }
                        result = state["result"]
                        if needs_review and not allow_region_shift and not dry_run:
                            raise PermissionError(
                                f"'{result['title']}' is of type {result['type']}: {REGION_WARNING}. "
                                "Pass allow_region_shift (--exam-ok) to replace its PDF anyway."
                            )

                        # "Replace PDF" is a styled label for this hidden file input.
                        # The Save button sits in the action bar, outside the <form> element.
                        file_input = page.locator("input[type='file'][name='template_pdf']")
                        save = page.get_by_role("button", name="Save Assignment")
                        if file_input.count() == 0 or save.count() == 0:
                            raise RuntimeError("Could not find the Replace PDF input or Save Assignment button")
                        file_input.set_input_files(pdf_path)
                        if dry_run:
                            logger.info(f"Dry run: ready to replace the PDF of '{result['title']}'; nothing submitted")
                            return result

                        save.click()
                        try:
                            page.wait_for_url("**/outline/edit", timeout=120000)
                        except PlaywrightTimeoutError as exc:
                            _expired(page)
                            raise RuntimeError(
                                f"Save Assignment did not lead to the outline page (now at {page.url})"
                            ) from exc
                        state["uploaded"] = result["uploaded"] = True
                        logger.info(f"Replaced the template PDF of '{result['title']}'")
                    else:
                        page.goto(outline_url)
                        _expired(page)

                    result = state["result"]
                    save_outline = page.get_by_role("button", name="Save Outline")
                    save_outline.wait_for(state="visible", timeout=60000)
                    with page.expect_response(
                        lambda r: r.request.method != "GET" and f"/assignments/{assignment_id}" in r.url,
                        timeout=120000,
                    ) as response_info:
                        save_outline.click()
                    response = response_info.value
                    if not response.ok and response.status not in (301, 302, 303):
                        raise RuntimeError(f"Save Outline failed: HTTP {response.status}")
                    state["outline_saved"] = result["outline_saved"] = True
                    logger.info(f"Saved the outline of '{result['title']}'")
                    return result
                finally:
                    browser.close()

        return _run_sync_in_thread(_do)
