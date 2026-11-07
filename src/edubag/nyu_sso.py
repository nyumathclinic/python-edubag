"""NYU single sign-on (Shibboleth -> Microsoft Entra -> Duo) helpers shared by clients.

NYU services (Brightspace, the AIM testing portal, ...) bounce through shibboleth.nyu.edu to a
Microsoft sign-in page, which may ask for MFA either by its own "Approve with MFA (Duo)" button or
by redirecting to Duo's hosted Universal Prompt. Afterwards Microsoft may ask "Stay signed in?"
(KMSI). A browser that answered yes to KMSI and was trusted by Duo carries persistent cookies
(Microsoft ``ESTSAUTHPERSISTENT``, Duo device trust) that let later logins skip the form and/or
the MFA challenge, so callers should start from their saved storage state when they have one.
"""

from __future__ import annotations

import re
import subprocess
import time
from re import Pattern

from loguru import logger
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

DUO_HOST = re.compile(r"https://[^/]*duosecurity\.com/")
DUO_BUTTON_NAME = "Approve with MFA (Duo)‎ You"


def notify_duo_push_sent(title: str = "NYU login needs Duo approval") -> None:
    """Fire a local desktop notification so a Duo push isn't missed."""
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                'display notification "Approve the push in Duo Mobile to continue." '
                f'with title "{title}" sound name "Ping"',
            ],
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Could not send desktop notification for Duo push (osascript unavailable).")


def handle_kmsi_interstitial(page) -> bool:
    """Accept Microsoft's 'Stay signed in?' prompt (with "don't show again") if it is showing.

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
        logger.debug("Handled Microsoft 'Stay signed in?' interstitial")
        return True
    return False


def approve_duo_if_challenged(page, timeout_ms: int = 8000, title: str | None = None) -> bool:
    """Click Microsoft's Duo push-approval button only if NYU actually challenges for MFA.

    A trusted browser/session can skip the Duo challenge entirely, in which case this button
    never appears. Returns True if a challenge was found and the push was triggered.
    """
    duo_button = page.get_by_role("button", name=DUO_BUTTON_NAME)
    try:
        duo_button.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return False

    duo_button.click()
    notify_duo_push_sent(*([title] if title else []))
    return True


def handle_duo_universal_prompt(page, title: str | None = None, _seen: set | None = None) -> bool:
    """Deal with Duo's hosted Universal Prompt, if the page is on it.

    Notifies the user once per prompt (the push is sent automatically, or the user picks a
    method), and answers "Yes, this is my device" to the trust question so later logins can
    skip Duo. Returns True if the page is on Duo.
    """
    if not DUO_HOST.match(page.url):
        return False
    seen = _seen if _seen is not None else set()
    if "notified" not in seen:
        seen.add("notified")
        logger.info("Duo is asking for approval; approve the push in Duo Mobile.")
        notify_duo_push_sent(*([title] if title else []))
    trust = page.get_by_role("button", name=re.compile(r"Yes, this is my device", re.I))
    try:
        if trust.count() > 0 and trust.first.is_visible():
            trust.first.click()
            logger.debug("Told Duo to trust this browser")
    except PlaywrightTimeoutError:
        pass
    return True


def wait_for_login_form_or_landing(page, landing: Pattern[str], timeout_ms: int = 20000) -> bool:
    """Poll until either the SSO email field or a page matching ``landing`` appears.

    A trusted browser/session can cause SSO to redirect straight to the landing page without
    ever showing a login form. Returns True if the landing page appeared first.
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    email_field = page.locator("input[type='email']")

    while time.monotonic() < deadline:
        if landing.match(page.url):
            return True
        if email_field.count() > 0 and email_field.first.is_visible():
            return False
        page.wait_for_timeout(250)

    raise PlaywrightTimeoutError("Timed out waiting for either the SSO login form or the landing page")


def wait_for_landing(page, landing: Pattern[str], timeout_ms: int = 120000, title: str | None = None) -> None:
    """Wait for a page matching ``landing``, handling KMSI and Duo's Universal Prompt on the way."""
    deadline = time.monotonic() + (timeout_ms / 1000)
    seen: set = set()

    while time.monotonic() < deadline:
        if landing.match(page.url):
            return
        if not handle_duo_universal_prompt(page, title=title, _seen=seen):
            # KMSI may appear after Duo; dismiss it and continue redirects.
            handle_kmsi_interstitial(page)
        page.wait_for_timeout(750)

    raise PlaywrightTimeoutError(f"Timed out waiting for post-login page matching {landing.pattern}")


def sso_login(
    page,
    landing: Pattern[str],
    username: str | None = None,
    password: str | None = None,
    timeout_ms: int = 120000,
    title: str | None = None,
) -> None:
    """Get ``page`` (already sent to an NYU SSO-protected URL) through login to ``landing``.

    Fills the Microsoft email/password form when it appears and credentials are given;
    otherwise asks the user to finish in the (headed) browser. Approves Duo when challenged.
    """
    page.wait_for_load_state("domcontentloaded", timeout=10000)
    if not wait_for_login_form_or_landing(page, landing):
        username_field = page.locator("input[type='email']")
        if username is not None:
            username_field.fill(username)
            page.get_by_role("button", name="Next").click()
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            password_field = page.locator("input[type='password']")
            password_field.wait_for(state="visible", timeout=10000)
            if password is not None:
                password_field.fill(password)
                page.get_by_role("button", name="Sign in").click()
                approve_duo_if_challenged(page, title=title)
            else:
                password_field.click()
                print("Please enter your password in the browser window and complete MFA.")
        else:
            username_field.click()
            print("Please enter your username and password in the browser window, then complete MFA.")

    wait_for_landing(page, landing, timeout_ms=timeout_ms, title=title)


def drop_session_cookies(storage_state: dict) -> dict:
    """A copy of a Playwright storage state without session cookies, like a browser restart.

    Persistent cookies (Microsoft "stay signed in", Duo device trust) survive, so SSO can
    usually complete silently, while every service starts a fresh session.
    """
    cookies = [c for c in storage_state.get("cookies", []) if c.get("expires", -1) not in (-1, None)]
    return {**storage_state, "cookies": cookies}
