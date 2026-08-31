#!/usr/bin/env python
"""Tests for Gradescope rubric discovery helpers."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock

from edubag.gradescope.discovery import probe_locator, slugify_step_name


def _load_discovery_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "discover_rubric_flow.py"
    spec = spec_from_file_location("discover_rubric_flow", script_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_slugify_step_name():
    assert slugify_step_name("Login Complete") == "login_complete"
    assert slugify_step_name("%%%") == "step"


def test_probe_locator_visible():
    locator = Mock()
    locator.count.return_value = 2
    locator.first.is_visible.return_value = True

    result = probe_locator(locator)

    assert result == {"count": 2, "visible": True}


def test_probe_locator_missing():
    locator = Mock()
    locator.count.return_value = 0

    result = probe_locator(locator)

    assert result == {"count": 0, "visible": False}


def test_discovery_script_parser_defaults():
    module = _load_discovery_script_module()
    parser = module.build_parser()

    args = parser.parse_args(["--course", "12345", "--assignment", "67890"])

    assert args.headless is False
    assert args.attempt_save is False
    assert args.term is None
    assert args.rubric_file is None


def test_discovery_script_parser_flags():
    module = _load_discovery_script_module()
    parser = module.build_parser()

    args = parser.parse_args(
        [
            "--course",
            "MATH-UA 122",
            "--assignment",
            "Quiz 1",
            "--headless",
            "--attempt-save",
            "--term",
            "Spring 2026",
        ]
    )

    assert args.headless is True
    assert args.attempt_save is True
    assert args.term == "Spring 2026"
