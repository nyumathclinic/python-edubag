#!/usr/bin/env python
"""Tests for brightspace client module."""

import os
import random
import tempfile
import time
from pathlib import Path

import pytest

from edubag.brightspace.client import BrightspaceClient


class TestBrightspaceClient:
    """Test the BrightspaceClient class."""

    def test_client_initialization(self):
        """Test basic client initialization."""
        client = BrightspaceClient()
        assert client.base_url == "https://brightspace.nyu.edu/"
        assert client.auth_state_path.name == "brightspace_auth.json"

    def test_client_custom_base_url(self):
        """Test client initialization with custom base URL."""
        custom_url = "https://custom.brightspace.com/"
        client = BrightspaceClient(base_url=custom_url)
        assert client.base_url == custom_url

    def test_client_custom_auth_path(self):
        """Test client initialization with custom auth state path."""
        custom_path = Path("/tmp/custom_auth.json")
        client = BrightspaceClient(auth_state_path=custom_path)
        assert client.auth_state_path == custom_path

    def test_default_auth_state_path(self):
        """Test the default auth state path generation."""
        path = BrightspaceClient._default_auth_state_path()
        assert path.name == "brightspace_auth.json"
        assert "edubag" in str(path)

    def test_check_export_checkbox_prefers_name_then_label(self):
        """Check that input name is tried before label fallbacks."""

        class FakeLocator:
            def __init__(self, count: int):
                self._count = count
                self.checked = False
                self.force = None

            def count(self):
                return self._count

            @property
            def first(self):
                return self

            def check(self, force: bool = False):
                self.checked = True
                self.force = force

        class FakePage:
            def __init__(self, name_counts: dict[str, int], label_counts: dict[str, int]):
                self.name_counts = name_counts
                self.label_counts = label_counts
                self.name_locators: dict[str, FakeLocator] = {}
                self.label_locators: dict[str, FakeLocator] = {}

            def locator(self, selector: str):
                name = selector.split("input[name='")[-1].split("']")[0]
                locator = FakeLocator(self.name_counts.get(name, 0))
                self.name_locators[name] = locator
                return locator

            def get_by_role(self, role: str, name: str | None = None, exact: bool | None = None):
                locator = FakeLocator(self.label_counts.get(name, 0) if name else 0)
                if name:
                    self.label_locators[name] = locator
                return locator

        page = FakePage(name_counts={"PointsGrade": 1}, label_counts={"Points grade": 1})
        found = BrightspaceClient._check_export_checkbox(
            page, name="PointsGrade", labels=("Points grade",)
        )
        assert found is True
        assert page.name_locators["PointsGrade"].checked is True
        assert page.name_locators["PointsGrade"].force is True

    def test_check_export_checkbox_returns_false_when_missing(self):
        """Return False when no checkbox labels match."""

        class FakeLocator:
            def __init__(self, count: int):
                self._count = count

            def count(self):
                return self._count

            @property
            def first(self):
                return self

            def check(self, force: bool = False):
                raise AssertionError("check should not be called when count is 0")

        class FakePage:
            def locator(self, selector: str):
                return FakeLocator(0)

            def get_by_role(self, role: str, name: str | None = None, exact: bool | None = None):
                return FakeLocator(0)

        page = FakePage()
        found = BrightspaceClient._check_export_checkbox(page, name="PointsGrade", labels=("Points grade",))
        assert found is False

    def test_grade_item_enter_urls_from_ids(self):
        """Build one Enter Grades URL per grade item ID, in order."""
        client = BrightspaceClient()
        prefix = "https://brightspace.nyu.edu/d2l/lms/grades/admin/enter/grade_item_edit.d2l"
        assert client._grade_item_enter_urls("611388", ["1940851", "1940852"]) == [
            f"{prefix}?objectId=1940851&ou=611388",
            f"{prefix}?objectId=1940852&ou=611388",
        ]

    def test_grade_item_enter_urls_single_string(self):
        """A single ID passed as a string is not split into characters."""
        urls = BrightspaceClient()._grade_item_enter_urls("611388", "1940851")
        assert len(urls) == 1
        assert urls[0].endswith("objectId=1940851&ou=611388")

    def test_grade_item_enter_urls_passthrough(self):
        """A full URL is used as-is."""
        url = "https://brightspace.nyu.edu/d2l/lms/grades/admin/enter/grade_item_edit.d2l?objectId=1&ou=2"
        assert BrightspaceClient()._grade_item_enter_urls(url) == [url]

    def test_grade_item_enter_urls_validation(self):
        """Reject missing grade item IDs or a URL combined with IDs."""
        client = BrightspaceClient()
        with pytest.raises(ValueError):
            client._grade_item_enter_urls("611388")
        with pytest.raises(ValueError):
            client._grade_item_enter_urls("https://brightspace.nyu.edu/x", ["1940851"])

    def test_clear_grades_skips_recorded_items(self, monkeypatch):
        """A re-auth retry resumes after items already done or failed."""
        client = BrightspaceClient(auth_state_path=Path(__file__))
        calls: list[list[str]] = []

        def fake_session(urls, done, failures, headless, dry_run):
            pending = [u for u in urls if u not in done and u not in failures]
            calls.append(pending)
            if len(calls) == 1:
                done.add(pending[0])
                failures[pending[1]] = "boom"
                raise RuntimeError("Authentication session expired.")
            done.update(pending)

        monkeypatch.setattr(client, "_clear_grades_session", fake_session)
        monkeypatch.setattr(client, "authenticate", lambda headless: None)
        failures = client.clear_grades("611388", ["1", "2", "3"])
        assert [len(c) for c in calls] == [3, 1]
        assert calls[1][0].endswith("objectId=3&ou=611388")
        assert list(failures.values()) == ["boom"]

    def test_save_gradebook_integration_private(self):
        """Private integration test: download gradebook for a real course.

        Requires RUN_BRIGHTSPACE_GRADEBOOK_TEST=1 and a valid auth state.
        """
        if os.getenv("RUN_BRIGHTSPACE_GRADEBOOK_TEST") != "1":
            return

        headless = os.getenv("BRIGHTSPACE_HEADLESS", "1") != "0"
        client = BrightspaceClient()
        if not client.auth_state_path.exists():
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            save_dir = Path(tmpdir)
            paths = client.save_gradebook(course="540180", save_dir=save_dir, headless=headless)
            assert paths
            for path in paths:
                assert path.exists()

    def test_save_gradebook_integration_private_repeated(self):
        """Run the private gradebook integration up to 10 times.

        Stops early on the first failure to surface intermittent errors.
        """
        if os.getenv("RUN_BRIGHTSPACE_GRADEBOOK_TEST") != "1":
            pytest.skip("RUN_BRIGHTSPACE_GRADEBOOK_TEST is not set")

        headless = os.getenv("BRIGHTSPACE_HEADLESS", "1") != "0"
        client = BrightspaceClient()
        if not client.auth_state_path.exists():
            pytest.skip("Brightspace auth state not found")

        for _ in range(10):
            with tempfile.TemporaryDirectory() as tmpdir:
                save_dir = Path(tmpdir)
                paths = client.save_gradebook(course="540180", save_dir=save_dir, headless=headless)
                assert paths
                for path in paths:
                    assert path.exists()
            time.sleep(random.uniform(0.1, 5.0))
