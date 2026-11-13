"""Tests for the Brightspace Import Grades and description-stamp helpers."""

import pytest

from edubag.brightspace import import_grades

BASE = "https://brightspace.nyu.edu/"
IMPORT = BASE + "d2l/lms/grades/admin/importexport/import/"
IMPORT_CAMEL = BASE + "d2l/lms/grades/admin/importExport/import/"


def test_urls():
    assert import_grades.file_select_url(BASE, "611388") == IMPORT + "file_select.d2l?ou=611388"
    assert import_grades.grade_objects_url(BASE, "611388").endswith("/d2l/api/le/1.80/611388/grades/")
    assert import_grades.grade_objects_url(BASE, "611388", 7).endswith("/611388/grades/7")


@pytest.mark.parametrize(
    "method, url, allow_import, allowed",
    [
        ("GET", IMPORT + "file_select.d2l?ou=1", False, True),
        ("POST", IMPORT + "file_select.d2l?d2l_rh=rpc&ou=1", False, True),
        ("POST", BASE + "d2l/common/upload/uploadFile.d2l?ou=1&fileName=x.csv", False, True),
        ("POST", BASE + "d2l/common/rpc/progress/progress.d2lfile?ou=1", False, True),
        ("POST", IMPORT_CAMEL + "errors_view.d2l?dirId=a&ou=1", False, True),
        ("POST", IMPORT_CAMEL + "errors_view.d2lfile?dirId=a&ou=1", False, True),
        # Only the preview page's own posts import grades.
        ("POST", IMPORT + "preview.d2l?dirId=a&ou=1", False, False),
        ("POST", IMPORT + "preview.d2l?dirId=a&ou=1", True, True),
        ("POST", IMPORT + "preview.d2lfile?dirId=a&ou=1", True, True),
        # Never anything else, even while importing.
        ("GET", BASE + "d2l/lms/grades/admin/enter/user_list_view.d2l?ou=1", True, False),
        ("POST", BASE + "d2l/lms/grades/admin/manage/item_props_newedit.d2l?objectId=1&ou=1", True, False),
        ("PUT", BASE + "d2l/api/le/1.80/1/grades/7", True, False),
    ],
)
def test_is_import_request(method, url, allow_import, allowed):
    assert import_grades.is_import_request(method, url, allow_import) is allowed


def test_summarize_preview_counts_changes_without_names():
    preview = {
        "items": ["Poll Participation Rate", "EdSTEM Activity"],
        "rows": [
            ["Doe, Jane", "94.118 / 100 -> 89.474 / 100", "0 / 1"],
            ["Roe, Rick", "100 / 100", "0 / 1 -> 2 / 1"],
            ["Poe, Pat", "50 / 100 -> 55 / 100", "1 / 1"],
            # Already imported; only the CSV's extra decimals differ.
            ["Loe, Lou", "94.444 / 100 -> 94.444 / 100", "0 / 1"],
            ["malformed"],
        ],
    }
    assert import_grades.summarize_preview(preview) == {
        "students": 4,
        "items": ["Poll Participation Rate", "EdSTEM Activity"],
        "changed": {"Poll Participation Rate": 2, "EdSTEM Activity": 1},
    }


STAMP = "<p><em>Last updated Fri Nov 13, 2026, 6:05 AM. Refreshed weekly, not live.</em></p>"


def test_apply_stamp_appends_once_and_replaces():
    original = "<p>Attendance is taken during each recitation.</p>"
    first = import_grades.apply_stamp(original, STAMP, "Last updated")
    assert first == original + "\n" + STAMP

    newer = STAMP.replace("Nov 13", "Nov 20")
    second = import_grades.apply_stamp(first, newer, "Last updated")
    assert second == original + "\n" + newer
    assert second.count("Last updated") == 1


def test_apply_stamp_finds_reformatted_stamp():
    # Brightspace may rewrite the stored HTML (attributes, nbsp, line breaks).
    stored = '<p>Keep me.</p>\n<p style="x"><em>Last&nbsp;updated\n Fri Nov 6</em></p>'
    assert import_grades.apply_stamp(stored, STAMP, "Last updated") == "<p>Keep me.</p>\n" + STAMP


def test_apply_stamp_empty_description_and_bad_stamp():
    assert import_grades.apply_stamp("", STAMP, "Last updated") == STAMP
    with pytest.raises(ValueError):
        import_grades.apply_stamp("<p>x</p>", "<p>Updated Nov 13</p>", "Last updated")


def test_same_html_ignores_whitespace_between_tags():
    assert import_grades.same_html("<p>a</p>\n<p>b</p>", "<p>a</p><p>b</p>")
    assert not import_grades.same_html("<p>a</p>", "<p>b</p>")


def test_grade_object_update_keeps_fields_and_drops_read_only():
    grade_object = {
        "Id": 7, "Name": "EdSTEM Activity", "ShortName": "", "GradeType": "Numeric",
        "CategoryId": 3, "MaxPoints": 1.0, "CanExceedMaxPoints": True, "IsBonus": False,
        "ExcludeFromFinalGradeCalculation": False, "GradeSchemeId": None,
        "GradeSchemeUrl": "/x", "Weight": 0, "AssociatedTool": None, "IsHidden": False,
        "Description": {"Text": "", "Html": "<p>old</p>"},
    }
    body = import_grades.grade_object_update(grade_object, "<p>new</p>")
    assert "Id" not in body and "GradeSchemeUrl" not in body
    assert body["Description"] == {"Content": "<p>new</p>", "Type": "Html"}
    assert body["MaxPoints"] == 1.0 and body["CanExceedMaxPoints"] is True


def test_description_html():
    assert import_grades.description_html({"Description": {"Text": "", "Html": "<p>x</p>"}}) == "<p>x</p>"
    assert import_grades.description_html({"Description": {"Text": "a < b", "Html": ""}}) == "a &lt; b"
    assert import_grades.description_html({}) == ""
