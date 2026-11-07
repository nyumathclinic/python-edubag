"""Base classes and protocols for LMS client implementations.

This module defines the common interface that all LMS clients should implement,
ensuring consistency across different learning management system integrations.
"""

import asyncio
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def run_sync_in_thread(func: Callable[..., T], *args, **kwargs) -> T:
    """Run sync Playwright code in a dedicated worker thread with its own event loop.

    Sync Playwright refuses to run inside a thread that already has a running asyncio loop
    (e.g. Jupyter); a fresh thread avoids that.
    """
    result: list = []
    error: list = []

    def wrapper() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result.append(func(*args, **kwargs))
        except BaseException as exc:  # re-raised in the caller's thread
            error.append(exc)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    thread = threading.Thread(target=wrapper, daemon=False)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


class LMSClient(ABC):
    """Abstract base class for LMS (Learning Management System) clients.

    All client implementations (Albert, Brightspace, Gradescope, etc.) should
    inherit from this class and implement the required abstract methods.

    Attributes:
        base_url: The base URL for the LMS platform
        auth_state_path: Path to the authentication state file for session persistence

    Note on `headless` parameter:
        Most methods accept a `headless` parameter that controls browser visibility:
        - `authenticate()`: defaults to `False` (headed mode) because authentication
          typically requires interactive steps like MFA, password entry, or CAPTCHA
        - Other operations (save_*, fetch_*, etc.): default to `True` (headless mode)
          because these are typically automated operations that benefit from running
          in the background without a visible browser window
    """

    base_url: str
    auth_state_path: Path

    @abstractmethod
    def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        headless: bool = False,
    ) -> None:
        """Authenticate to the LMS platform and save session state.

        Args:
            username: Username/NetID for login. If None, user must enter manually.
            password: Password for login. If None, user must enter manually.
            headless: Run browser in headless mode. Defaults to False because
                authentication typically requires interactive steps (MFA, etc.).

        Raises:
            RuntimeError: If authentication fails.

        Note:
            Authentication state is persisted to `self.auth_state_path` for reuse
            in subsequent operations without requiring re-authentication.
        """
        ...

    @staticmethod
    @abstractmethod
    def _default_auth_state_path() -> Path:
        """Get the platform-appropriate default path for the auth state file.

        Returns:
            Path to the default authentication state file location.
        """
        ...
