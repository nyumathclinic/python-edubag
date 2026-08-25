"""Module to automate interactions with the Albert learning platform."""

import json
import os
import re
from collections.abc import Generator
from pathlib import Path
from urllib.parse import urlencode

import platformdirs
from loguru import logger
from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from edubag.albert.term import Term
from edubag.clients import LMSClient


def _normalize_label(label: str) -> str:
    """Convert a label to snake_case variable name format.

    Args:
        label: The label text to normalize.

    Returns:
        A snake_case version of the label.
    """
    # Convert to lowercase and replace non-word characters with underscores
    # Strip leading/trailing underscores
    normalized = re.sub(r"[^\w]+", "_", label.lower()).strip("_")
    return normalized


class AlbertClient(LMSClient):
    """Client to interact with the Albert learning platform.

    This client provides automated browser-based interactions with NYU's Albert
    system for fetching course rosters, class details, and other academic data.

    Note on headless parameter:
        Methods that accept `headless` parameter default to:
        - `False` for `authenticate()` - interactive login with MFA required
        - `True` for other operations - automated scraping benefits from headless mode
    """

    base_url = "https://sis.portal.nyu.edu/psp/ihprod/EMPLOYEE/EMPL/?cmd=start"
    course_base_url = (
        "https://sis.nyu.edu/psc/csprod/EMPLOYEE/SA/c/"
        "NYU_SR_FL.NYU_CLASSROSTER_FL.GBL"
    )

    @staticmethod
    def _default_auth_state_path() -> Path:
        """Get the platform-appropriate default path for the auth state file."""
        cache_dir = Path(platformdirs.user_cache_dir("edubag", "NYU"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "albert_auth.json"

    def __init__(self, base_url: str | None = None, auth_state_path: Path | None = None):
        """Initializes the AlbertClient."""
        if base_url is not None:
            self.base_url = base_url
        if auth_state_path is not None:
            self.auth_state_path = auth_state_path
        else:
            self.auth_state_path = self._default_auth_state_path()

    @staticmethod
    def _course_url(
        course_base_url: str,
        class_number: int | str,
        term: int | str | Term,
        instructor_id: int | str | None = None,
    ) -> str:
        """Build the Albert class roster URL for a course."""
        if isinstance(term, Term):
            term_code = term.code
        elif isinstance(term, int) or term.isdigit():
            term_code = int(term)
        else:
            term_code = Term.from_name(term).code

        resolved_instructor_id = (
            instructor_id if instructor_id is not None else os.getenv("ALBERT_INSTRUCTOR_ID")
        )
        if resolved_instructor_id is None:
            raise ValueError(
                "instructor_id must be provided or ALBERT_INSTRUCTOR_ID must be set"
            )

        query = urlencode(
            {
                "Page": "NYU_FACCLSRST_NUFL",
                "Action": "U",
                "ExactKeys": "Y",
                "INSTRUCTOR_ID": resolved_instructor_id,
                "INSTITUTION": "NYUNV",
                "CLASS_NBR": class_number,
                "STRM": term_code,
            }
        )
        return f"{course_base_url}?{query}"

    def authenticate(self, username: str | None = None, password: str | None = None, headless=False) -> None:
        """Log into Albert and save the authentication state.

        Args:
            username (str | None): NetID to log in with. If None, user must enter manually in browser.
            password (str | None): Password for login. If None, user must enter manually in browser.
            headless (bool): Whether to run the browser in headless mode. Headless mode requires username and password.

        Raises:
            RuntimeError: If authentication fails.
        """
        if username is None or password is None:
            headless = False
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context()
            page = context.new_page()

            page.goto(self.base_url)

            # Wait for page to load and form to appear instead of URL (SAML can redirect)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            # Wait for the username input field to appear and be visible
            page.locator("input[type='email']").wait_for(state="visible", timeout=10000)

            if username is not None:
                page.locator("input[type='email']").fill(username)
                page.get_by_role("button", name="Next").click()

                if password is not None:
                    # Wait for password field to appear
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    page.locator("input[type='password']").wait_for(state="visible", timeout=10000)
                    page.locator("input[type='password']").fill(password)
                    page.get_by_role("button", name="Sign in").click()
                    page.get_by_role("button", name="Approve with MFA (Duo)‎ You").click()
                else:
                    print("Please enter your password in the browser window and complete MFA.")
            else:
                print("Please enter your username and password in the browser window, then complete MFA.")

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
                logger.debug("Handled 'Stay signed in?' interstitial during Albert authentication")

            # SAML and MFA redirects can be slow; wait for the Albert landing URL pattern.
            page.wait_for_url(
                re.compile(r".*/h/\?tab=IS_FSA_TAB.*"),
                timeout=120000,
                wait_until="domcontentloaded",
            )

            context.storage_state(path=self.auth_state_path)
            logger.debug(f"Authentication state saved at {self.auth_state_path}")

            browser.close()

    def _get_courses_paginated(self, page: Page, course_name: str) -> Generator[Locator]:
        """Iterator that yields course locators across all pages with pagination.

        Args:
            page: The Playwright page object.
            course_name: The name of the course to filter by.

        Yields:
            Course locators matching the course name across all pages.
        """
        while True:
            courses = (
                page.locator(
                    "div.isFSA_SchCrsWrp",
                    has=page.get_by_role("heading", name=course_name),
                )
                .locator("visible=true")
                .all()
            )
            yield from courses

            # Check if there's a next page button and click it
            next_button = page.locator(".isFSA_PNext")
            if next_button.count() > 0 and next_button.is_visible():
                logger.debug("Navigating to next page of courses")
                next_button.click()
                page.wait_for_load_state("networkidle")
            else:
                break

    def _save_roster_for_course(self, course: Locator, save_path: Path | None = None) -> Path:
        """Process a course and save its roster.

        Args:
            course: A course locator element.
            save_path: Directory to save the roster. If None, saves to current directory.

        Returns:
            Path to the saved roster file.
        """
        with course.page.expect_popup() as popup_info:
            course.get_by_role("link", name="Class Roster").click()
        roster_page = popup_info.value
        roster_page.wait_for_url(re.compile(r".*PortalActualURL=.*"))
        download_file_path = self._save_roster_page(roster_page, save_path)
        roster_page.close()
        return download_file_path

    def _save_roster_page(self, roster_page: Page, save_path: Path | None = None) -> Path:
        """Download a roster from an already-open Albert roster page."""
        section_name = roster_page.locator("#DERIVED_SSR_FC_CLASS_SECTION").text_content()
        section_name = section_name.strip() if section_name else ""
        logger.info(f"Processing roster for section: {section_name}")
        roster_page.get_by_label("Print/Download Options").select_option("EXL")
        with roster_page.expect_download() as download_info:
            with roster_page.expect_popup():
                roster_page.get_by_role("button", name="Generate").click()
        download = download_info.value
        if save_path is not None:
            save_path.mkdir(parents=True, exist_ok=True)
            download_file_path = save_path / download.suggested_filename
        else:
            download_file_path = Path(download.suggested_filename)
        logger.info(f"Downloading roster to {download_file_path}")
        download.save_as(download_file_path)
        return download_file_path

    def _extract_class_details_from_container(self, container: Locator | Page) -> dict:
        """Extract class details from a container element or page.

        Args:
            container: A Locator or Page containing elements with class psc_has_value.

        Returns:
            Dictionary with extracted class detail information.
        """
        class_details = {}
        elements = container.locator(".psc_has_value").all()

        for element in elements:
            # Find the label within this element
            label_element = element.locator(".ps-label")
            if label_element.count() == 0:
                continue

            label_text = label_element.first.text_content()
            if label_text:
                label_text = label_text.strip()

            # If label is empty or just whitespace (like &nbsp;), extract from label element's parent ID
            if not label_text or label_text == "\xa0":  # \xa0 is non-breaking space
                label_parent = label_element.first.locator("xpath=parent::*")
                if label_parent.count() > 0:
                    label_parent_id = label_parent.first.get_attribute("id")
                    if label_parent_id:
                        match = re.search(r"win0div([A-Z0-9_]+)lbl", label_parent_id)
                        if match:
                            label_text = match.group(1)
                        else:
                            continue
                    else:
                        continue
                else:
                    continue

            if not label_text:
                continue

            # Find the value within this element - try ps_box-value first
            value_element = element.locator(".ps_box-value")
            value_text = None

            if value_element.count() > 0:
                value_text = value_element.first.text_content()
                if value_text is not None:
                    value_text = value_text.strip()

            # If no ps_box-value found or it's None, try ps_box-value-readonly or other variations
            if value_text is None:
                # Try alternative class names
                for class_name in [".ps_box-value-readonly", "[id*='$span']"]:
                    value_element = element.locator(class_name)
                    if value_element.count() > 0:
                        value_text = value_element.first.text_content()
                        if value_text is not None:
                            value_text = value_text.strip()
                            break

            # If we still don't have a value, skip this element
            if value_text is None:
                continue

            # Clean up the value text: replace non-breaking spaces with regular spaces and collapse multiple spaces
            if value_text:
                value_text = value_text.replace("\xa0", " ")
                value_text = re.sub(r" +", " ", value_text)
                value_text = value_text.strip()

            # Skip elements with empty or whitespace-only values
            if not value_text:
                continue

            # Normalize the label to snake_case
            normalized_label = _normalize_label(label_text)
            # map funny labels to more standard ones
            label_mappings = {
                "derived_clsrch_descr200": "full_course_name",
                "derived_clsrch_descrlong": "description",
                "ssr_cls_dtl_wrk_ssr_cls_txb_msg": "textbook_message",
            }
            if normalized_label in label_mappings:
                normalized_label = label_mappings[normalized_label]

            # Try to convert to integer if the value is a clean integer string.
            # Values like '123ABC' or '12.5' will be kept as strings.
            # Values starting with '0' (except exactly '0') are kept as strings to preserve leading zeros.
            # This is intentional to preserve data as-is unless clearly numeric.
            # Empty strings are kept as-is.
            if value_text:
                # Keep strings with leading zeros (except exactly "0") as strings
                if value_text.startswith("0") and len(value_text) > 1:
                    value = value_text
                else:
                    try:
                        value = int(value_text)
                    except ValueError:
                        value = value_text
            else:
                value = value_text

            class_details[normalized_label] = value

            # Special parsing for derived_ssr_fc_descr254: "Course Name (class_number) (class_type)"
            if normalized_label == "derived_ssr_fc_descr254" and isinstance(value, str):
                # Match pattern: text (text) (text)
                match = re.match(r"^(.+?)\s*\(([^)]+)\)\s*\(([^)]+)\)$", value)
                if match:
                    course_name = match.group(1).strip()
                    # class_number = match.group(2).strip()  # Already have this
                    class_type = match.group(3).strip()
                    class_details["course_name"] = course_name
                    class_details["class_type"] = class_type
                    del class_details[normalized_label]

            # Special parsing for derived_clsrch_sss_page_keydescr: "School | Term | Type"
            if normalized_label == "derived_clsrch_sss_page_keydescr" and isinstance(value, str):
                # Extract the school (part before the first |)
                parts = value.split("|")
                if parts:
                    school = parts[0].strip()
                    class_details["school"] = school
                del class_details[normalized_label]

        return class_details

    def _fetch_course_class_details(self, course: Locator) -> dict:
        """Fetch class detail information for a course.

        Args:
            course: A course locator element.

        Returns:
            Dictionary with class detail information.
        """
        with course.page.expect_popup() as popup_info:
            course.get_by_role("link", name="Class Roster").click()
        roster_page = popup_info.value
        roster_page.wait_for_url(re.compile(r".*PortalActualURL=.*"))
        class_details = self._extract_course_details_from_roster_page(roster_page)
        roster_page.close()
        return class_details

    def _extract_course_details_from_roster_page(self, roster_page: Page) -> dict:
        """Extract class details from an already-open Albert roster page."""

        class_details = {}
        # Extract class details from the roster page header
        roster_metadata = roster_page.locator("#win0divROSTER_HDRGRP")
        if roster_metadata.count() > 0:
            roster_details = self._extract_class_details_from_container(roster_metadata)
            class_details.update(roster_details)
            logger.debug(f"Extracted {len(roster_details)} fields from roster page header")

        # Extract class details from the roster page metadata section
        roster_metadata = roster_page.locator("#win0divCLASS_MTG_NBR1\\$0")
        if roster_metadata.count() > 0:
            roster_details = self._extract_class_details_from_container(roster_metadata)
            class_details.update(roster_details)
            logger.debug(f"Extracted {len(roster_details)} fields from roster page")

        # Click "Class meeting information"
        roster_page.get_by_text("Class meeting information").click()
        roster_page.wait_for_load_state("networkidle")

        # Click "Full Class Detail" and wait until an actual H1 with text "Class Detail" is visible
        roster_page.get_by_text("View Full Class Detail").click()
        roster_page.locator("h1:has-text('Class Detail')").wait_for(state="visible", timeout=30000)

        # Extract all elements from the full class detail page
        detail_page_data = self._extract_class_details_from_container(roster_page)
        class_details.update(detail_page_data)
        logger.debug(f"Extracted {len(detail_page_data)} fields from full class detail page")

        self._reconcile_stale_header_section(class_details)

        return class_details

    @staticmethod
    def _reconcile_stale_header_section(class_details: dict) -> None:
        """Correct `section` when it lags the roster header component.

        The roster page header (`#win0divROSTER_HDRGRP`) is read immediately
        after navigation and can still reflect whichever class was last
        loaded in this authenticated session, one fetch behind the class
        just requested -- this shows up when scraping several classes in a
        row via direct URLs. `full_course_name` (e.g. "MATH-UA 120 - 020
        Discrete Mathematics") comes from a part of the page that has
        consistently reflected the class actually requested, so use it to
        detect and correct a stale `section` value.
        """
        full_course_name = class_details.get("full_course_name")
        if not isinstance(full_course_name, str):
            return
        match = re.search(r"\s-\s*(\d{2,4})\s", full_course_name)
        if not match:
            return
        derived_section = match.group(1)
        header_section = class_details.get("section")
        if header_section is not None and str(header_section).strip() == derived_section:
            return
        logger.warning(
            "Roster header 'section' looks stale (got "
            f"'{header_section}', full_course_name implies '{derived_section}'); "
            "correcting. Other header-only fields (class_type, instructor, "
            "days_and_times) may still be stale for this class."
        )
        class_details["section"] = derived_section

    def _fetch_direct_roster_session(
        self,
        class_number: int | str,
        term: str | Term,
        instructor_id: int | str | None = None,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> Path:
        """Fetch and save a roster from a direct Albert course URL."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=self.auth_state_path, accept_downloads=True)
            page = context.new_page()
            page.goto(self.base_url)
            if "login" in page.url or "errorCode" in page.url:
                browser.close()
                raise RuntimeError("Authentication session expired.")
            page.wait_for_load_state("networkidle")
            page.goto(self._course_url(self.course_base_url, class_number, term, instructor_id))
            page.wait_for_load_state("networkidle")
            result = self._save_roster_page(page, save_dir)
            browser.close()
            return result

    def fetch_roster(
        self,
        class_number: int | str,
        term: str | Term,
        instructor_id: int | str | None = None,
        save_dir: Path | None = None,
        username: str | None = None,
        password: str | None = None,
        headless: bool = True,
    ) -> Path:
        """Fetch and save a class roster using Albert's direct course URL.

        Args:
            class_number: Albert's class number.
            term: Academic term name or :class:`Term` instance.
            instructor_id: Albert instructor ID. Falls back to ``ALBERT_INSTRUCTOR_ID``.
            save_dir: Directory for the downloaded roster.
            username: NetID used if authentication is required.
            password: Password used if authentication is required.
            headless: Whether to run the browser headlessly.
        """
        if not self.auth_state_path.exists():
            self.authenticate(username=username, password=password, headless=headless)

        for attempt in range(2):
            try:
                return self._fetch_direct_roster_session(
                    class_number, term, instructor_id, save_dir, headless
                )
            except (TimeoutError, PlaywrightTimeoutError, RuntimeError):
                if attempt == 1:
                    raise
                if self.auth_state_path.exists():
                    self.auth_state_path.unlink()
                self.authenticate(username=username, password=password, headless=headless)
        raise RuntimeError("Unable to fetch roster")

    def _fetch_direct_course_details_session(
        self,
        class_number: int | str,
        term: str | Term,
        instructor_id: int | str | None = None,
        headless: bool = True,
    ) -> dict:
        """Fetch course details from a direct Albert course URL."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=self.auth_state_path)
            page = context.new_page()
            page.goto(self.base_url)
            if "login" in page.url or "errorCode" in page.url:
                browser.close()
                raise RuntimeError("Authentication session expired.")
            page.wait_for_load_state("networkidle")
            page.goto(self._course_url(self.course_base_url, class_number, term, instructor_id))
            page.wait_for_load_state("networkidle")
            result = self._extract_course_details_from_roster_page(page)
            browser.close()
            return result

    def fetch_course_details(
        self,
        class_number: int | str,
        term: str | Term,
        instructor_id: int | str | None = None,
        username: str | None = None,
        password: str | None = None,
        headless: bool = True,
        output: Path | None = None,
    ) -> dict:
        """Fetch course details using Albert's direct course URL."""
        if not self.auth_state_path.exists():
            self.authenticate(username=username, password=password, headless=headless)

        for attempt in range(2):
            try:
                result = self._fetch_direct_course_details_session(
                    class_number, term, instructor_id, headless
                )
                if output is not None:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with output.open("w") as output_file:
                        json.dump(result, output_file, indent=2)
                return result
            except (TimeoutError, PlaywrightTimeoutError, RuntimeError):
                if attempt == 1:
                    raise
                if self.auth_state_path.exists():
                    self.auth_state_path.unlink()
                self.authenticate(username=username, password=password, headless=headless)
        raise RuntimeError("Unable to fetch course details")

    def _fetch_rosters_session(
        self,
        course_name: str,
        term: str | Term,
        save_dir: Path | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Internal method to fetch rosters in a single browser session.

        Raises TimeoutError if the session times out.
        Raises RuntimeError if authentication has expired.
        """
        result_paths = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=self.auth_state_path, accept_downloads=True)
            page = context.new_page()

            page.goto(self.base_url)
            if "login" in page.url or "errorCode" in page.url:
                browser.close()
                raise RuntimeError("Authentication session expired.")

            try:
                page.locator("#IS_FSA_SchWrp").get_by_role("link", name=str(term)).click()
            except Exception as e:
                # The click failed - check if we got redirected to login
                error_message = str(e)
                current_url = page.url
                logger.debug(f"Click failed. Current URL: {current_url}")
                logger.debug(f"Error message: {error_message}")
                # Check both the current URL and the error message for login/auth failures
                if (
                    "login" in current_url
                    or "errorCode" in current_url
                    or "errorCode=105" in error_message
                    or "cmd=login" in error_message
                ):
                    logger.info("Detected authentication failure in error message")
                    browser.close()
                    raise RuntimeError("Authentication session expired during navigation.") from e
                # If not a login redirect, re-raise the original exception
                logger.debug("Not an auth error, re-raising original exception")
                browser.close()
                raise

            page.wait_for_load_state("networkidle")

            # Process all courses across all pages
            for course in self._get_courses_paginated(page, course_name):
                download_path = self._save_roster_for_course(course, save_dir)
                result_paths.append(download_path)

            browser.close()
        return result_paths

    def fetch_and_save_rosters(
        self,
        course_name: str,
        term: str | Term,
        save_dir: Path | None = None,
        username: str | None = None,
        password: str | None = None,
        headless: bool = True,
    ) -> list[Path]:
        """Fetch from the network and save to disk the class rosters for a given
        course offering.

        Args:
          * course_name (str): The name of the course.
          * term (str | Term): The term of the course.
          * save_dir (Path | None): Directory to save the rosters. If None,
            uses default directory.
          * username (str | None): NetID to log in with. If None, user must enter manually.
          * password (str | None): Password for login. If None, user must enter manually.
          * headless (bool): Whether to run the browser in headless mode.

        Returns:
            List[Path]: List of paths to the downloaded roster files.
        """
        # Check if authentication state exists; if not, authenticate first
        if not self.auth_state_path.exists():
            logger.warning(f"Auth state file not found at {self.auth_state_path}. Running authentication...")
            self.authenticate(username=username, password=password, headless=headless)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                return self._fetch_rosters_session(course_name, term, save_dir, headless)
            except (TimeoutError, RuntimeError) as e:
                if attempt < max_retries:
                    logger.warning(f"{type(e).__name__}: {e} Authentication may have expired.")
                    logger.info("Re-authenticating...")
                    if self.auth_state_path.exists():
                        self.auth_state_path.unlink()
                    self.authenticate(username=username, password=password, headless=headless)
                else:
                    logger.error(f"Max retries exceeded. {type(e).__name__}: {e}")
                    raise
        return []

    def _fetch_class_details_session(
        self,
        course_name: str,
        term: str | Term,
        headless: bool = True,
    ) -> list[dict]:
        """Internal method to fetch class details in a single browser session.

        Raises TimeoutError if the session times out.
        Raises RuntimeError if authentication has expired.
        """
        result = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=self.auth_state_path)
            page = context.new_page()

            page.goto(self.base_url)
            if "login" in page.url or "errorCode" in page.url:
                browser.close()
                raise RuntimeError("Authentication session expired.")

            try:
                page.locator("#IS_FSA_SchWrp").get_by_role("link", name=str(term)).click()
            except Exception as e:
                # The click failed - check if we got redirected to login
                error_message = str(e)
                current_url = page.url
                logger.debug(f"Click failed. Current URL: {current_url}")
                logger.debug(f"Error message: {error_message}")
                # Check both the current URL and the error message for login/auth failures
                if (
                    "login" in current_url
                    or "errorCode" in current_url
                    or "errorCode=105" in error_message
                    or "cmd=login" in error_message
                ):
                    logger.info("Detected authentication failure in error message")
                    browser.close()
                    raise RuntimeError("Authentication session expired during navigation.") from e
                # If not a login redirect, re-raise the original exception
                logger.debug("Not an auth error, re-raising original exception")
                browser.close()
                raise

            page.wait_for_load_state("networkidle")

            # Process all courses across all pages
            for course in self._get_courses_paginated(page, course_name):
                class_details = self._fetch_course_class_details(course)
                result.append(class_details)

            browser.close()
        return result

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
            except (TimeoutError, RuntimeError) as e:
                if attempt < max_retries:
                    logger.warning(f"{type(e).__name__}: {e} Authentication may have expired.")
                    logger.info("Re-authenticating...")
                    if self.auth_state_path.exists():
                        self.auth_state_path.unlink()
                    self.authenticate(username=username, password=password, headless=headless)
                else:
                    logger.error(f"Max retries exceeded. {type(e).__name__}: {e}")
                    raise
        return []

    def _find_academic_engagement_link(self, page: Page, class_number: int, term_code: int) -> Locator | None:
        """Find the Academic Engagement link for a specific class across paginated pages.

        Args:
            page: The Playwright page object.
            class_number: The class number to find.
            term_code: The term code to match.

        Returns:
            Locator for the Academic Engagement link, or None if not found.
        """
        while True:
            # Look for Academic Engagement links with matching class number and term
            links = page.locator("a.isFSA_SchAe_Btn").all()
            for link in links:
                onclick_attr = link.get_attribute("onclick")
                if onclick_attr and "NYU_ACADEMIC_ENGAGEMENT_FLUID" in onclick_attr:
                    # Check if the onclick contains the correct class number and term
                    if f"CLASS_NBR={class_number}" in onclick_attr and f"STRM={term_code}" in onclick_attr:
                        logger.info(f"Found Academic Engagement link for class {class_number} term {term_code}")
                        return link

            # Check if there's a next page button and click it
            next_button = page.locator(".isFSA_PNext")
            if next_button.count() > 0 and next_button.is_visible():
                logger.debug("Navigating to next page to find Academic Engagement link")
                next_button.click()
                page.wait_for_load_state("networkidle")
            else:
                logger.warning(f"Academic Engagement link not found for class {class_number} term {term_code}")
                return None

    def _mark_engaged_session(
        self,
        class_number: int,
        term: str | Term,
        email_addresses: list[str],
        headless: bool = True,
    ) -> None:
        """Internal method to mark students as engaged in a single browser session.

        Args:
            class_number: The class number for the course.
            term: The academic term.
            email_addresses: List of email addresses to mark as engaged.
            headless: Whether to run the browser in headless mode.

        Raises:
            TimeoutError: If the session times out.
            RuntimeError: If authentication has expired or engagement link not found.
        """
        if isinstance(term, str):
            term = Term.from_name(term)
        term_code = term.code

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=self.auth_state_path)
            page = context.new_page()

            page.goto(self.base_url)
            if "login" in page.url or "errorCode" in page.url:
                browser.close()
                raise RuntimeError("Authentication session expired.")

            try:
                page.locator("#IS_FSA_SchWrp").get_by_role("link", name=str(term)).click()
            except Exception as e:
                error_message = str(e)
                current_url = page.url
                logger.debug(f"Click failed. Current URL: {current_url}")
                logger.debug(f"Error message: {error_message}")
                if (
                    "login" in current_url
                    or "errorCode" in current_url
                    or "errorCode=105" in error_message
                    or "cmd=login" in error_message
                ):
                    logger.info("Detected authentication failure in error message")
                    browser.close()
                    raise RuntimeError("Authentication session expired during navigation.") from e
                logger.debug("Not an auth error, re-raising original exception")
                browser.close()
                raise

            page.wait_for_load_state("networkidle")

            # Find the Academic Engagement link
            engagement_link = self._find_academic_engagement_link(page, class_number, term_code)
            if engagement_link is None:
                browser.close()
                raise RuntimeError(f"Academic Engagement link not found for class {class_number} in term {term}")

            # Click the Academic Engagement link
            engagement_link.click()
            page.wait_for_load_state("networkidle")

            # Wait for the iframe to load
            iframe_locator = page.locator("iframe[name='lbFrameContent']")
            iframe_locator.wait_for(state="visible", timeout=30000)
            frame = page.frame_locator("iframe[name='lbFrameContent']")

            # Wait a bit for the grid to populate
            page.wait_for_timeout(2000)

            # Mark each student as engaged
            marked_count = 0
            for email in email_addresses:
                logger.debug(f"Looking for student with email {email}")
                # Find the row containing the student's email by looking for the email link
                # Using a more precise selector to avoid substring matches
                rows = frame.locator("tr.ps_grid-row").all()

                matched_row = None
                for row in rows:
                    # Look for the email link within the row
                    email_link = row.locator(f"a[href='javascript:LaunchURL(null,\\'mailto:{email}\\',5);']")
                    if email_link.count() > 0:
                        matched_row = row
                        break

                    # Also try looking for any link containing the email as a fallback
                    if matched_row is None:
                        any_email_link = row.locator(f"a:has-text('{email}')")
                        if any_email_link.count() > 0:
                            matched_row = row
                            logger.debug(f"Found student {email} by text content")
                            break

                if matched_row is None:
                    logger.warning(f"Student with email {email} not found in engagement list")
                    # Log all emails found on the page for debugging
                    all_emails = frame.locator("tr.ps_grid-row a").all()
                    if all_emails:
                        logger.debug(f"Emails found on page: {[link.text_content() for link in all_emails[:10]]}")
                    continue

                # Find the engagement checkbox in this row
                checkbox_container = matched_row.locator(".psc_off_container").first
                if checkbox_container.count() > 0:
                    checkbox_container.click()
                    marked_count += 1
                    logger.info(f"Marked {email} as engaged")
                else:
                    logger.warning(f"Engagement checkbox not found for {email}")

            logger.info(f"Marked {marked_count} out of {len(email_addresses)} students as engaged")

            # Submit the form
            submit_button = frame.locator("a[id='NYU_AE_RSTR_WRK_SUBMIT_PB']")
            submit_button.click()
            page.wait_for_load_state("networkidle")

            # Click Yes to confirm the submission is accurate
            yes_button = frame.get_by_role("button", name="Yes", exact=True)
            yes_button.click()
            page.wait_for_load_state("networkidle")

            logger.info("Academic engagement submission completed")
            browser.close()

    def mark_engaged(
        self,
        class_number: int,
        term: str | Term,
        email_addresses: list[str],
        username: str | None = None,
        password: str | None = None,
        headless: bool = True,
    ) -> None:
        """Mark students as engaged in academic activities for a course.

        Args:
            class_number: The class number for the course.
            term: The academic term (e.g., "Spring 2026" or Term object).
            email_addresses: List of email addresses to mark as engaged.
            username: NetID to log in with. If None, user must enter manually.
            password: Password for login. If None, user must enter manually.
            headless: Whether to run the browser in headless mode.

        Raises:
            RuntimeError: If authentication fails or engagement link not found.
        """
        # Check if authentication state exists; if not, authenticate first
        if not self.auth_state_path.exists():
            logger.warning(f"Auth state file not found at {self.auth_state_path}. Running authentication...")
            self.authenticate(username=username, password=password, headless=headless)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                self._mark_engaged_session(class_number, term, email_addresses, headless)
                return
            except (TimeoutError, RuntimeError) as e:
                if attempt < max_retries:
                    logger.warning(f"{type(e).__name__}: {e} Authentication may have expired.")
                    logger.info("Re-authenticating...")
                    if self.auth_state_path.exists():
                        self.auth_state_path.unlink()
                    self.authenticate(username=username, password=password, headless=headless)
                else:
                    logger.error(f"Max retries exceeded. {type(e).__name__}: {e}")
                    raise
