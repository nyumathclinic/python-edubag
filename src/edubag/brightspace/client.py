"""Module to automate interactions with the Brightspace learning platform."""

import asyncio
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

import platformdirs
from loguru import logger
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

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


class BrightspaceClient(LMSClient):
    """Client to interact with the Brightspace learning platform.

    This client provides automated browser-based interactions with NYU's Brightspace
    (D2L) learning management system for downloading gradebooks, attendance, and
    other course data.

    Note on headless parameter:
        Methods that accept `headless` parameter default to:
        - `False` for `authenticate()` - interactive login with MFA required
        - `True` for other operations - automated downloads benefit from headless mode
    """

    @staticmethod
    def _default_auth_state_path() -> Path:
        """Get the platform-appropriate default path for the auth state file."""
        cache_dir = Path(platformdirs.user_cache_dir("edubag", "NYU"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "brightspace_auth.json"

    def __init__(self, base_url: str | None = None, auth_state_path: Path | None = None):
        """Initializes the BrightspaceClient."""
        if base_url is not None:
            self.base_url = base_url
        else:
            self.base_url = "https://brightspace.nyu.edu/"
        if auth_state_path is not None:
            self.auth_state_path = auth_state_path
        else:
            self.auth_state_path = self._default_auth_state_path()

    @staticmethod
    def _handle_kmsi_interstitial(page) -> bool:
        """Handle Microsoft 'Stay signed in?' prompt if it appears.

        Returns True when the interstitial was detected and submitted.
        """
        kmsi_heading = page.locator("div[role='heading']", has_text="Stay signed in?")
        kmsi_checkbox = page.locator("#KmsiCheckboxField")
        kmsi_submit = page.locator("#idSIButton9")

        heading_visible = kmsi_heading.count() > 0 and kmsi_heading.first.is_visible()
        checkbox_visible = kmsi_checkbox.count() > 0 and kmsi_checkbox.first.is_visible()
        if not heading_visible and not checkbox_visible:
            return False

        if checkbox_visible and not kmsi_checkbox.first.is_checked():
            kmsi_checkbox.first.check()
        if kmsi_submit.count() > 0 and kmsi_submit.first.is_visible():
            kmsi_submit.first.click()
            logger.debug("Handled 'Stay signed in?' interstitial during Brightspace authentication")
            return True
        return False

    @staticmethod
    def _wait_for_login_form_or_landing(page, timeout_ms: int = 20000) -> bool:
        """Poll until either the SSO email field or the D2L landing page appears.

        A trusted browser/session can cause SSO to redirect straight to the
        Brightspace landing page without ever showing a login form.

        Returns True if the landing page appeared first (fully trusted session,
        no login form was shown at all).
        """
        deadline = time.monotonic() + (timeout_ms / 1000)
        brightspace_url = re.compile(r"https://brightspace\.nyu\.edu/d2l/.*")
        email_field = page.locator("input[type='email']")

        while time.monotonic() < deadline:
            if brightspace_url.match(page.url):
                return True
            if email_field.count() > 0 and email_field.first.is_visible():
                return False
            page.wait_for_timeout(250)

        raise PlaywrightTimeoutError(
            "Timed out waiting for either the SSO login form or the Brightspace landing page"
        )

    @staticmethod
    def _notify_duo_push_sent() -> None:
        """Fire a local desktop notification so a Duo push isn't missed."""
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'display notification "Approve the push in Duo Mobile to continue." '
                    'with title "Brightspace login needs Duo approval" sound name "Ping"',
                ],
                check=False,
                timeout=5,
            )
        except OSError:
            logger.debug("Could not send desktop notification for Duo push (osascript unavailable).")

    @staticmethod
    def _approve_duo_if_challenged(page, timeout_ms: int = 8000) -> bool:
        """Click the Duo push-approval button only if NYU actually challenges for MFA.

        A trusted browser/session can skip the Duo challenge entirely, in which
        case this button never appears. Returns True if a challenge was found
        and the push was triggered.
        """
        duo_button = page.get_by_role("button", name="Approve with MFA (Duo)‎ You")
        try:
            duo_button.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            return False

        duo_button.click()
        BrightspaceClient._notify_duo_push_sent()
        return True

    def _wait_for_brightspace_landing(self, page, timeout_ms: int = 120000) -> None:
        """Wait for Brightspace landing while opportunistically handling KMSI prompt."""
        deadline = time.monotonic() + (timeout_ms / 1000)
        brightspace_url = re.compile(r"https://brightspace\.nyu\.edu/d2l/.*")

        while time.monotonic() < deadline:
            if brightspace_url.match(page.url):
                return

            # KMSI may appear after Duo; dismiss it and continue redirects.
            self._handle_kmsi_interstitial(page)
            page.wait_for_timeout(750)

        raise PlaywrightTimeoutError("Timed out waiting for Brightspace post-login landing page")

    def _run_with_reauth(self, operation: Callable[[], list[Path]], headless: bool) -> list[Path]:
        """Run an operation with one re-authentication retry on auth expiration."""
        if not self.auth_state_path.exists():
            logger.warning(
                f"Auth state file not found at {self.auth_state_path}. Running authentication..."
            )
            self.authenticate(headless=headless)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                return operation()
            except RuntimeError as e:
                if attempt < max_retries:
                    logger.warning(f"RuntimeError: {e} Authentication may have expired.")
                    logger.info("Re-authenticating...")
                    self.authenticate(headless=headless)
                    continue

                logger.error(f"Max retries exceeded. RuntimeError: {e}")
                raise

        return []

    def authenticate(self, username: str | None = None, password: str | None = None, headless: bool = False) -> None:
        """Log into Brightspace and save the authentication state.

        Args:
            username (str | None): NetID to log in with. If None, user must enter manually in browser.
            password (str | None): Password for login. If None, user must enter manually in browser.
            headless (bool): Whether to run the browser in headless mode. Headless mode requires username and password.

        Raises:
            RuntimeError: If authentication fails.
        """
        if username is None or password is None:
            headless = False
        
        def _do_authenticate():
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                if self.auth_state_path.exists():
                    # Reuse any cached cookies (e.g. a persistent Microsoft/Duo
                    # "trust this browser" cookie) so a trusted browser can skip
                    # straight past the login form and/or the Duo challenge
                    # instead of being forced through both on every re-auth.
                    context = browser.new_context(storage_state=self.auth_state_path)
                else:
                    context = browser.new_context()
                page = context.new_page()

                page.goto(self.base_url)

                # Wait for page to load and form to appear instead of URL (SAML can redirect)
                page.wait_for_load_state("domcontentloaded", timeout=10000)

                # A trusted session may redirect straight to the Brightspace
                # landing page without ever showing the login form.
                already_landed = self._wait_for_login_form_or_landing(page)

                if not already_landed:
                    username_field = page.locator("input[type='email']")

                    if username is not None:
                        username_field.fill(username)
                        page.get_by_role("button", name="Next").click()
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                        page.locator("input[type='password']").wait_for(state="visible", timeout=10000)
                        password_field = page.locator("input[type='password']")

                        if password is not None:
                            # Wait for password field to appear
                            password_field.fill(password)
                            page.get_by_role("button", name="Sign in").click()
                            # A trusted session may skip the Duo challenge entirely.
                            self._approve_duo_if_challenged(page)
                        else:
                            # interactive mode: focus password field and wait for user to enter password
                            password_field.click()
                            print("Please enter your password in the browser window and complete MFA.")
                    else:
                        # interactive mode: focus username field and wait for user to enter credentials
                        username_field.click()
                        print("Please enter your username and password in the browser window, then complete MFA.")

                # Wait for Brightspace landing after SSO/MFA and optional KMSI interstitial.
                self._wait_for_brightspace_landing(page, timeout_ms=120000)

                context.storage_state(path=self.auth_state_path)
                logger.debug(f"Authentication state saved at {self.auth_state_path}")

                browser.close()
        
        _run_sync_in_thread(_do_authenticate)

    @staticmethod
    def _check_export_checkbox(
        page,
        *,
        name: str | None = None,
        labels: tuple[str, ...] = (),
    ) -> bool:
        """Check the first matching export checkbox by input name or label.

        Returns True if a checkbox was found and checked.
        """
        locator = None
        if name:
            name_locator = page.locator(f"input[name='{name}']")
            if name_locator.count() > 0:
                locator = name_locator.first
        if locator is None:
            for label in labels:
                label_locator = page.get_by_role("checkbox", name=label, exact=False)
                if label_locator.count() > 0:
                    locator = label_locator.first
                    break
        if locator is None:
            return False
        try:
            locator.check(force=True)
        except PlaywrightError:
            # Some D2L checkboxes (e.g. the gradebook export's "Select all
            # rows") are driven by a custom onclick handler rather than plain
            # native toggling, and Playwright's coordinate-based synthetic
            # click sometimes fails to trigger it ("did not change its
            # state"). Fall back to invoking the element's own click()
            # method, which runs the same activation behavior (including the
            # onclick handler) without relying on hit-testing at a specific
            # screen position.
            locator.evaluate("el => { if (!el.checked) el.click(); }")
        return True

    def _save_gradebook_session(
        self,
        course: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Internal method to save gradebook in a single browser session.

        Raises RuntimeError if authentication has expired.
        """
        def _do_save_gradebook():
            result_paths = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path, accept_downloads=True)
                page = context.new_page()

                # Navigate to the course page
                # Determine if course is a full URL or just an ID
                if course.startswith("http://") or course.startswith("https://"):
                    course_url = course
                else:
                    course_url = f"{self.base_url}d2l/home/{course}"
                page.goto(course_url)

                # Check if we need to re-login
                if "login" in page.url:
                    logger.error("Authentication session expired. Please re-authenticate.")
                    browser.close()
                    raise RuntimeError("Authentication session expired.")

                # Navigate to Grades
                page.get_by_role("link", name="Grades").click()

                enter_grades_url = re.compile(r".*grades/admin/enter/user_list_view\.d2l.*")
                if not enter_grades_url.search(page.url):
                    # "Grades" usually opens a flyout submenu with an "Enter
                    # Grades" link. D2L appends a hidden " selected" suffix to
                    # that link's accessible name only when it's already the
                    # active nav item, so match on the stable "Enter Grades"
                    # substring instead of requiring that suffix. For some
                    # course configurations (e.g. coordinated/cross-listed
                    # sections with only one grades tool enabled), clicking
                    # "Grades" navigates straight to the Enter Grades page
                    # instead of opening a flyout, so tolerate that link never
                    # appearing -- the wait_for_url below is the real check.
                    try:
                        page.get_by_role("link", name="Enter Grades").first.click(timeout=10000)
                    except PlaywrightError:
                        pass

                page.wait_for_url("**/grades/admin/enter/user_list_view.d2l**", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=30000)

                # Export gradebook
                export_to_csv = None
                for _ in range(3):
                    try:
                        export_button = page.get_by_role("button", name="Export")
                        export_button.wait_for(state="visible", timeout=10000)
                        export_button.scroll_into_view_if_needed()
                        export_button.click()

                        page.get_by_role("button", name="Sections").click()
                        clear_button = page.get_by_role("button", name="Clear").first
                        if clear_button.count() > 0:
                            try:
                                if clear_button.is_enabled():
                                    clear_button.click()
                            except PlaywrightError:
                                pass
                        page.get_by_role("button", name="Apply").click()

                        export_to_csv = page.get_by_role("button", name="Export to CSV")
                        export_to_csv.wait_for(state="visible", timeout=5000)
                        break
                    except PlaywrightError:
                        page.wait_for_timeout(1000)
                if export_to_csv is None:
                    raise RuntimeError("Export menu did not appear; please retry headed mode.")
                if not self._check_export_checkbox(
                    page,
                    name="PointsGrade",
                    labels=("Points grade", "Points Grade", "Points"),
                ):
                    logger.warning("Export option 'Points grade' not found.")
                if not self._check_export_checkbox(page, name="LastName", labels=("Last Name",)):
                    logger.warning("Export option 'Last Name' not found.")
                if not self._check_export_checkbox(page, name="FirstName", labels=("First Name",)):
                    logger.warning("Export option 'First Name' not found.")
                if not self._check_export_checkbox(page, name="Email", labels=("Email",)):
                    logger.warning("Export option 'Email' not found.")
                if not self._check_export_checkbox(
                    page,
                    name="SectionMembership",
                    labels=("Section Membership", "Section"),
                ):
                    logger.warning("Export option 'Section Membership' not found.")
                if not self._check_export_checkbox(
                    page,
                    labels=("Select all rows", "Select All Rows"),
                ):
                    logger.warning("Export option 'Select all rows' not found.")
                export_to_csv.scroll_into_view_if_needed()
                export_to_csv.click()

                with page.expect_download() as download_info:
                    page.get_by_role("button", name="Download").click()
                download = download_info.value

                # Save the download
                if save_dir is not None:
                    save_dir.mkdir(parents=True, exist_ok=True)
                    download_file_path = save_dir / download.suggested_filename
                else:
                    download_file_path = Path(download.suggested_filename)
                logger.info(f"Downloading gradebook to {download_file_path}")
                download.save_as(download_file_path)
                result_paths.append(download_file_path)

                page.get_by_role("button", name="Close").click()

                browser.close()
            return result_paths
        
        return _run_sync_in_thread(_do_save_gradebook)

    def save_gradebook(
        self,
        course: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Fetch from the network and save to disk the complete gradebook for a given
        course offering.

        Args:
          * course: The course ID or full URL to the course
          * save_dir: directory to save the file in (default: current working directory)
          * headless: Whether to run the browser in headless mode

        Returns:
            list[Path]: Paths to the downloaded gradebook files.
        """
        return self._run_with_reauth(
            lambda: self._save_gradebook_session(course, save_dir, headless),
            headless=headless,
        )

    def _save_attendance_session(
        self,
        course: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Internal method to save attendance in a single browser session.

        Raises RuntimeError if authentication has expired.
        """
        def _do_save_attendance():
            result_paths = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(storage_state=self.auth_state_path, accept_downloads=True)
                page = context.new_page()

                # Navigate to the course page
                # Determine if course is a full URL or just an ID
                if course.startswith("http://") or course.startswith("https://"):
                    course_url = course
                else:
                    course_url = f"{self.base_url}d2l/home/{course}"
                page.goto(course_url)

                # Check if we need to re-login
                if "login" in page.url:
                    logger.error("Authentication session expired. Please re-authenticate.")
                    browser.close()
                    raise RuntimeError("Authentication session expired.")

                # Navigate to Attendance
                page.get_by_role("button", name="More Tools").click()
                page.get_by_role("link", name="Attendance").click()
                page.wait_for_load_state("domcontentloaded", timeout=10000)

                # Exit early if there are no attendance registers available
                empty_state = page.locator(".empty-state-container").first
                if empty_state.is_visible():
                    logger.info("No attendance registers available; nothing to download.")
                    browser.close()
                    return result_paths

                # Process each attendance register
                attendance_links = page.get_by_title("View attendance data in ").all()
                if not attendance_links:
                    logger.info("No attendance registers found; nothing to download.")
                    browser.close()
                    return result_paths
                for loc in attendance_links:
                    loc.click()
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    # Get the attendance name from the h1 heading
                    attendance_name = page.locator("h1").inner_text()
                    logger.info(f"Processing {attendance_name}")
                    logger.debug(f"Processing attendance register at {page.url}")
                    page.get_by_role("button", name="Export All Data").click()

                    with page.expect_download() as download2_info:
                        # Download link lives inside the export dialog iframe; target it directly
                        iframe = page.frame_locator("iframe[title='Export Attendance Data']").first
                        download_link = iframe.locator(".dfl a").first
                        download_link.wait_for(state="visible", timeout=10000)
                        download_link.click()
                    download2 = download2_info.value

                    # Save the download
                    if save_dir is not None:
                        save_dir.mkdir(parents=True, exist_ok=True)
                        download_file_path = save_dir / download2.suggested_filename
                    else:
                        download_file_path = Path(download2.suggested_filename)
                    logger.info(f"Downloading attendance register to {download_file_path}")
                    download2.save_as(download_file_path)
                    result_paths.append(download_file_path)

                    page.get_by_role("button", name="Close").click()
                    page.get_by_role("button", name="Done").click()

                browser.close()
            return result_paths
        
        return _run_sync_in_thread(_do_save_attendance)

    def save_attendance(
        self,
        course: str,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Fetch from the network and save to disk the attendance registers for a given
        course offering.

        Args:
          * course: The course ID or full URL to the course
          * save_dir: directory to save the file in (default: current working directory)
          * headless: Whether to run the browser in headless mode

        Returns:
            list[Path]: Paths to the downloaded attendance register files.
        """
        return self._run_with_reauth(
            lambda: self._save_attendance_session(course, save_dir, headless),
            headless=headless,
        )
