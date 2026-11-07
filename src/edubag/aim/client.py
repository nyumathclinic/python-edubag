"""Browser automation for the AIM instructor portal (NYU Moses Center testing accommodations).

Login goes ``/NYU/`` -> NYU SSO (shibboleth -> Microsoft -> Duo) -> ``/NYU/`` ->
``instructor/Login.aspx`` (a FERPA acknowledgement: "Continue to View Student Accommodations")
-> ``instructor/Overview.aspx``.

AIM's own cookies (and shibboleth's) are all session cookies, and an AIM session that has gone
idle, or a cold deep link into the portal, sends the browser through SAML single logout, which
also signs Microsoft out. So every run behaves like a fresh browser: it starts from the saved
storage state *minus its session cookies* and always enters at ``/NYU/``. The persistent
Microsoft "stay signed in" and Duo device-trust cookies usually let SSO finish without any
prompt; when they don't, :meth:`AimClient.authenticate` (headed) signs in again.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TypeVar

import platformdirs
from loguru import logger
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from edubag import nyu_sso
from edubag.aim import portal
from edubag.aim.portal import ExamFilter, ExamRow
from edubag.clients import LMSClient, run_sync_in_thread

T = TypeVar("T")

ON_AIM = re.compile(r"https://[^/]*accessiblelearning\.com/NYU/", re.I)
LOGGED_OUT = re.compile(r"/logout|/SLO\b|SAML2/Redirect/SLO", re.I)
DUO_TITLE = "AIM login needs Duo approval"
DOWNLOAD_TIMEOUT_S = 600  # ~12 MB per exam, up to 10 per ZIP


class AimError(Exception):
    """A problem with the request or page (not an expired login, so no re-authentication)."""


@dataclass
class DownloadBatch:
    """One "Download Selected Exams" ZIP: the rows checked, and the files it held."""

    rows: list[ExamRow]
    zip_path: Path
    files: list[Path] = field(default_factory=list)


class AimClient(LMSClient):
    """Client for the AIM instructor portal at bachelor.accessiblelearning.com/NYU."""

    base_url = "https://bachelor.accessiblelearning.com/NYU/"

    @staticmethod
    def _default_auth_state_path() -> Path:
        cache_dir = Path(platformdirs.user_cache_dir("edubag", "NYU"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "aim_auth.json"

    def __init__(self, base_url: str | None = None, auth_state_path: Path | None = None):
        if base_url is not None:
            self.base_url = base_url.rstrip("/") + "/"
        self.auth_state_path = auth_state_path or self._default_auth_state_path()

    # ------------------------------------------------------------------ session plumbing

    @property
    def completed_url(self) -> str:
        return self.base_url + "instructor/Testing.aspx?Step=Completed"

    def _fresh_state(self) -> dict | None:
        """Saved storage state without session cookies (a 'new browser'), or None."""
        if not self.auth_state_path.exists():
            return None
        try:
            return nyu_sso.drop_session_cookies(json.loads(self.auth_state_path.read_text()))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Ignoring unreadable auth state {self.auth_state_path}: {e}")
            return None

    def _new_context(self, browser):
        state = self._fresh_state()
        return browser.new_context(accept_downloads=True, **({"storage_state": state} if state else {}))

    def _enter_portal(
        self,
        page,
        username: str | None,
        password: str | None,
        interactive: bool,
    ) -> None:
        """From a fresh context, get to the instructor portal past SSO and the FERPA page.

        Raises RuntimeError when the login can't finish non-interactively (or AIM logs out),
        which callers treat as "re-authenticate".
        """
        page.goto(self.base_url)
        page.wait_for_load_state("domcontentloaded")
        if LOGGED_OUT.search(page.url):
            raise RuntimeError(f"AIM sent the browser to a logout page ({page.url})")
        try:
            landed = nyu_sso.wait_for_login_form_or_landing(page, ON_AIM)
        except PlaywrightTimeoutError:
            landed = False
        if not landed and not interactive and not (username and password):
            raise RuntimeError("NYU SSO wants an interactive login")
        if not landed:
            nyu_sso.sso_login(page, ON_AIM, username, password, title=DUO_TITLE)

        page.goto(self.base_url + "instructor/")
        page.wait_for_load_state("domcontentloaded")
        if LOGGED_OUT.search(page.url) or not ON_AIM.match(page.url):
            raise RuntimeError(f"AIM did not open the instructor portal (at {page.url})")
        ferpa = page.locator(f"#{portal.FERPA_CONTINUE_ID}")
        if ferpa.count():
            with page.expect_navigation():
                ferpa.click()
            logger.debug("Acknowledged the FERPA notice")

    def _open_completed(self, page) -> str:
        """Go to Testing > Completed with all rows on one page; return its HTML."""
        page.goto(self.completed_url)
        page.wait_for_load_state("domcontentloaded")
        if "Testing.aspx" not in page.url:
            raise RuntimeError(f"AIM did not open the Completed exams page (at {page.url})")
        html = page.content()
        total = portal.parse_record_count(html)
        select = page.locator(f"#{portal.PAGE_SIZE_ID}")
        if total and select.count():
            options = [int(v) for v in select.locator("option").evaluate_all("os => os.map(o => o.value)")]
            current = int(select.input_value())
            if current < total:
                size = next((o for o in sorted(options) if o >= total), max(options))
                with page.expect_navigation():
                    select.select_option(str(size))
                page.wait_for_load_state("domcontentloaded")
                html = page.content()
                logger.debug(f"Showing {size} rows per page for {total} records")
        rows = portal.parse_completed_table(html)
        if total and len(rows) < total:
            raise AimError(f"Only {len(rows)} of {total} completed exams are on one page")
        return html

    def _run_with_reauth(self, operation: Callable[[], T], headless: bool) -> T:
        """Run once; if the login has lapsed, authenticate interactively and run again."""
        try:
            return operation()
        except RuntimeError as e:
            logger.warning(f"{e}; signing in to AIM again...")
            self.authenticate(headless=headless)
            return operation()

    # ------------------------------------------------------------------ public API

    def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        headless: bool = False,
    ) -> None:
        """Sign in through NYU SSO (completing MFA as needed) and save the storage state.

        Without both credentials the browser is shown so the user can finish signing in.
        """
        if not (username and password):
            headless = False

        def _do() -> None:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = self._new_context(browser)
                page = context.new_page()
                if not headless:
                    print("Finish signing in to AIM in the browser window if asked.")
                self._enter_portal(page, username, password, interactive=True)
                context.storage_state(path=self.auth_state_path)
                logger.debug(f"AIM authentication state saved at {self.auth_state_path}")
                browser.close()

        run_sync_in_thread(_do)

    def _session(
        self,
        work: Callable[..., T],
        headless: bool,
        username: str | None,
        password: str | None,
        term: str | None,
    ) -> T:
        def _do() -> T:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = self._new_context(browser)
                page = context.new_page()
                try:
                    self._enter_portal(page, username, password, interactive=False)
                    html = self._open_completed(page)
                    shown = portal.parse_term(html)
                    if term and shown and shown.casefold() != term.casefold():
                        raise AimError(f"AIM shows term {shown!r}, not {term!r}")
                    result = work(page, html)
                    context.storage_state(path=self.auth_state_path)  # refresh persistent cookies
                    return result
                finally:
                    browser.close()

        return self._run_with_reauth(lambda: run_sync_in_thread(_do), headless)

    def list_exams(
        self,
        filter: ExamFilter | None = None,  # noqa: A002
        headless: bool = True,
        username: str | None = None,
        password: str | None = None,
        term: str | None = None,
    ) -> list[ExamRow]:
        """Completed exams (optionally only those matching ``filter``) for the current term."""

        def work(page, html):
            rows = portal.parse_completed_table(html)
            return [r for r in rows if filter is None or filter.matches(r)]

        return self._session(work, headless, username, password, term)

    def save_exams(
        self,
        save_dir: Path,
        filter: ExamFilter | None = None,  # noqa: A002
        keys: set[str] | None = None,
        want: Callable[[ExamRow], bool] | None = None,
        zip_dir: Path | None = None,
        headless: bool = True,
        username: str | None = None,
        password: str | None = None,
        term: str | None = None,
    ) -> list[DownloadBatch]:
        """Download completed exams and extract their PDFs into ``save_dir``.

        Takes the rows matching ``filter``, whose :attr:`ExamRow.key` is in ``keys`` (if given)
        and for which ``want(row)`` is true (if given; e.g. to skip exams already downloaded),
        in ZIPs of up to 10 (the portal's limit). ZIPs are kept in ``zip_dir``
        (default ``save_dir/_zips``).
        """
        save_dir = Path(save_dir)
        zip_dir = Path(zip_dir) if zip_dir else save_dir / "_zips"

        def work(page, html):
            rows = [
                r
                for r in portal.parse_completed_table(html)
                if (filter is None or filter.matches(r))
                and (keys is None or r.key in keys)
                and (want is None or want(r))
            ]
            if not rows:
                return []
            wanted = [r.key for r in rows]
            batches = []
            for i, chunk_keys in enumerate(portal.chunks(wanted)):
                if i:  # fresh page for each ZIP: the download button is click-once
                    html = self._open_completed(page)
                by_key = {r.key: r for r in portal.parse_completed_table(html)}
                missing = [k for k in chunk_keys if k not in by_key]
                if missing:
                    raise AimError(f"Exams disappeared from the Completed list: {missing}")
                chunk = [by_key[k] for k in chunk_keys]
                batches.append(self._download_chunk(page, chunk, save_dir, zip_dir))
            return batches

        return self._session(work, headless, username, password, term)

    def _download_chunk(self, page, rows: list[ExamRow], save_dir: Path, zip_dir: Path) -> DownloadBatch:
        for r in rows:
            page.locator(f"#{r.checkbox_id}").check()
        downloads: list = []

        def on_download(download) -> None:  # Playwright can't wrap builtins like list.append
            downloads.append(download)

        page.on("download", on_download)
        page.locator(f"#{portal.DOWNLOAD_BUTTON_ID}").click()
        dialog = page.locator(".ui-dialog:visible")
        deadline = datetime.now().timestamp() + DOWNLOAD_TIMEOUT_S
        while not downloads:
            if dialog.count():
                message = " ".join(dialog.first.inner_text().split())
                raise AimError(f"AIM refused the download: {message}")
            if datetime.now().timestamp() > deadline:
                raise AimError(f"No download after {DOWNLOAD_TIMEOUT_S}s")
            page.wait_for_timeout(500)
        download = downloads[0]
        zip_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        zip_path = zip_dir / f"{stamp}-{download.suggested_filename}"
        download.save_as(zip_path)
        files = portal.extract_zip(zip_path, save_dir)
        logger.info(f"Downloaded {len(rows)} exam(s) as {zip_path.name}: {[f.name for f in files]}")
        page.remove_listener("download", on_download)
        return DownloadBatch(rows=rows, zip_path=zip_path, files=files)
