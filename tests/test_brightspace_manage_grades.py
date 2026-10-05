"""Tests for the Brightspace Manage Grades crawl helpers."""

from datetime import date, datetime, time

import pytest

from edubag.brightspace import manage_grades

BASE = "https://brightspace.nyu.edu/"


def test_manage_grades_url():
    assert manage_grades.manage_grades_url(BASE, "611388") == (
        "https://brightspace.nyu.edu/d2l/lms/grades/admin/manage/gradeslist.d2l?ou=611388"
    )


def test_edit_tab_urls_by_kind():
    item = manage_grades.edit_tab_urls(BASE, "611388", "item", 1940968)
    assert list(item) == ["properties", "restrictions", "objectives"]
    assert item["restrictions"].endswith("item_rests_edit.d2l?objectId=1940968&ou=611388")
    assert list(manage_grades.edit_tab_urls(BASE, "611388", "category", 1)) == ["properties", "restrictions"]
    assert list(manage_grades.edit_tab_urls(BASE, "611388", "final_grade", 1)) == ["properties"]
    assert manage_grades.edit_tab_urls(BASE, "611388", "unknown", 1) == {}


def test_edit_tab_urls_never_enter_grades():
    for kind in manage_grades.EDIT_TABS:
        for url in manage_grades.edit_tab_urls(BASE, "611388", kind, 1).values():
            assert "/enter/" not in url


@pytest.mark.parametrize(
    "method, url, allowed",
    [
        ("GET", BASE + "d2l/lms/grades/admin/manage/item_props_newedit.d2l?objectId=1&ou=2", True),
        ("GET", BASE + "d2l/lms/grades/admin/enter/grade_item_edit.d2l?objectId=1&ou=2", False),
        ("GET", BASE + "d2l/lms/grades/admin/enter/user_list_view.d2l?ou=2", False),
        ("POST", BASE + "d2l/lms/grades/admin/manage/item_rests_edit.d2l?objectId=1&ou=2", False),
        ("POST", BASE + "d2l/api/oslo/batch?languageId=1", True),
        ("PUT", BASE + "d2l/api/le/1.0/2/grades/1", False),
    ],
)
def test_is_read_only_request(method, url, allowed):
    assert manage_grades.is_read_only_request(method, url) is allowed


GRADE_LIST_HTML = """
<table>
<tr><th scope="col"></th><th scope="col">Grade Item</th><th scope="col">Type</th><th scope="col">Max. Points</th></tr>
<tr><td><input type="checkbox" name="GradesList_cb" value="i1_10"></td>
  <th scope="row"><a onclick="gotoNewEditCatProps( 10 );;return false;" href="javascript://">Quizzes</a></th>
  <td></td><td>24</td></tr>
<tr><td><input type="checkbox" name="GradesList_cb" value="i2_11"></td>
  <th scope="row" style="padding-left:3rem;"><a onclick="gotoNewEditItemProps( 11 );;return false;" href="javascript://" title="Edit Quiz 1">Quiz 1</a>
  <img title="Available on Oct 2, 2026 5:00 PM"></th>
  <td>Numeric</td><td>12</td></tr>
<tr><td><input type="checkbox" name="GradesList_cb" value="i3_12"></td>
  <th scope="row"><a onclick="gotoNewEditItemProps( 12 );;return false;" href="javascript://">Engagement</a></th>
  <td>Selectbox</td><td>1</td></tr>
<tr><td><input type="checkbox" name="GradesList_cb" value="i4_13"></td>
  <th scope="row"><a onclick="gotoNewEditFinalGradeProps( 13 );;return false;" href="javascript://">Final Calculated Grade</a></th>
  <td></td><td>100</td></tr>
</table>
"""

TAB_PANEL_HTML = """
<d2l-tab-panel _selected="">
<table class="d_FG"><tbody>
<tr><td><h2>General</h2></td></tr>
<tr><td class="fl_n"><label class="d2l-label"><span>Type</span></label></td><td></td></tr>
<tr><td class="fct_w"><label>Numeric</label></td></tr>
<tr><td class="fl_n"><label class="d2l-label" for="n"><span>Name</span></label></td><td></td></tr>
<tr><td class="fct_w"><input id="n" type="text" name="Name1" value="Quiz 1"></td></tr>
<tr><td><h2>Grading</h2></td></tr>
<tr><td class="fl_n"><label class="d2l-label"><span>Weight</span></label></td><td></td></tr>
<tr><td class="fct_w"><d2l-input-number id="w" data-hidden-id="wh" label="Weight" value="0"></d2l-input-number>
  <input type="hidden" id="wh" value="3.5"></td></tr>
<tr><td class="fl_n"><label class="d2l-label"><span>Rubrics</span></label></td><td></td></tr>
<tr><td class="fct_w"><table class="dcs"><tr><td class="d2l-selector-emptytext">No rubrics selected.</td></tr></table></td></tr>
</tbody></table>
<div><div class="d2l-checkbox-container"><input type="checkbox" id="hs"><label for="hs">Has Start Date</label></div>
  <input type="hidden" id="d$isEnabled" value="0">
  <d2l-input-date-time id="d" label="Date" value="2026-10-05T11:37:00.000"></d2l-input-date-time></div>
</d2l-tab-panel>
"""


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except sync_api.Error as e:
            pytest.skip(f"Chromium not available: {e}")
        yield browser.new_page()
        browser.close()


def test_extract_grade_list(page):
    page.set_content(GRADE_LIST_HTML)
    result = page.evaluate(manage_grades.EXTRACT_GRADE_LIST_JS)
    rows = {r["name"]: r for r in result["rows"]}
    assert [r["kind"] for r in result["rows"]] == ["category", "item", "item", "final_grade"]
    assert rows["Quiz 1"]["id"] == 11
    assert rows["Quiz 1"]["category"] == {"id": 10, "name": "Quizzes"}
    assert rows["Quiz 1"]["columns"] == {"Type": "Numeric", "Max. Points": "12"}
    assert rows["Quiz 1"]["indicators"] == ["Available on Oct 2, 2026 5:00 PM"]
    assert rows["Engagement"]["category"] is None


def test_extract_tab_panel(page):
    page.set_content(TAB_PANEL_HTML)
    result = page.evaluate(manage_grades.EXTRACT_TAB_PANEL_JS)
    fields = {(f["field"], f.get("label")): f for f in result["fields"]}
    assert fields[("Name", "Name")]["value"] == "Quiz 1"
    assert fields[("Name", "Name")]["section"] == "General"
    weight = fields[("Weight", "Weight")]
    assert (weight["value"], weight["displayed"], weight["section"]) == ("3.5", "0", "Grading")
    assert fields[("Type", None)]["value"] == "Numeric"
    start = next(f for f in result["fields"] if f["control"] == "d2l-input-date-time")
    assert (start["label"], start["enabled"]) == ("Start Date", False)
    assert result["lists"] == [
        {"section": "Grading", "field": "Rubrics", "rows": [], "empty_text": "No rubrics selected."}
    ]


@pytest.mark.parametrize(
    "day, expected",
    [
        ("2026-09-03", "2026-09-04"),  # Thursday -> next day
        ("2026-09-08", "2026-09-11"),  # Tuesday -> same week
        ("2026-09-04", "2026-09-11"),  # Friday -> the following Friday
        ("2026-12-10", "2026-12-11"),
    ],
)
def test_following_weekday(day, expected):
    assert manage_grades.following_weekday(date.fromisoformat(day)) == datetime.combine(
        date.fromisoformat(expected), time(12, 0)
    )


@pytest.mark.parametrize(
    "method, url, allowed",
    [
        ("POST", BASE + "d2l/lms/grades/admin/manage/item_rests_edit.d2l?objectId=1&ou=2", True),
        ("POST", BASE + "d2l/lms/grades/admin/manage/item_props_newedit.d2l?objectId=1&ou=2", False),
        ("POST", BASE + "d2l/lms/grades/admin/enter/grade_item_edit.d2l?objectId=1&ou=2", False),
        ("GET", BASE + "d2l/lms/grades/admin/enter/user_list_view.d2l?ou=2", False),
        ("GET", BASE + "d2l/lms/grades/admin/manage/item_rests_edit.d2l?objectId=1&ou=2", True),
    ],
)
def test_is_restrictions_write(method, url, allowed):
    assert manage_grades.is_restrictions_write(method, url) is allowed
