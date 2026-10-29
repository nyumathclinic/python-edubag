"""Tests for the Brightspace quiz report helpers."""

import json
from urllib.parse import parse_qs, urlparse

from edubag.brightspace import quizzes

BASE = "https://brightspace.nyu.edu/"

# Trimmed from a quiz's View Reports page.
REPORT_LIST_HTML = """
<table id="z_c" class="d2l-table d2l-grid d_gl" type="list"><tbody>
<tr class="d2l-table-row-first"><td class="d_gn"><a class="d2l-link d2l-link-inline"
  href="/d2l/lms/quizzing/reports/quiz_report_build.d2l?isprv=&amp;qi=519757&amp;ri=10178&amp;cfql=0&amp;ou=611388"
  title="View Report">Question Details including text responses</a></td></tr>
<tr class="d_detailsRow"><td class="d_gd" colspan="1">
  <div class="dco"><div class="dco_c"><label><strong>Report Type:</strong> </label><label>Question Details</label></div></div>
  <div class="dco"><div class="dco_c"><label><strong>Release:</strong> </label><label>immediately</label></div></div>
</td></tr>
<tr><td class="d_gn"><a class="d2l-link d2l-link-inline"
  href="/d2l/lms/quizzing/reports/quiz_report_build.d2l?isprv=&amp;qi=519757&amp;ri=10179&amp;cfql=0&amp;ou=611388"
  title="View Report">User Attempts</a></td></tr>
<tr class="d_detailsRow d2l-table-row-last"><td class="d_gd" colspan="1">
  <div class="dco"><div class="dco_c"><label><strong>Report Type:</strong> </label><label>User Attempts</label></div></div>
</td></tr>
</tbody></table>
"""

ALERT_HTML = """
<ul class="d2l-datalist vui-list">
<li class="d2l-datalist-item"><div class="d2l-datalist-item-content">
  <a class="d2l-link" id="d2l_2_1_658"
    href="/d2l/lms/quizzing/reports/quiz_report_export_summary.d2l?ou=611388&amp;success=true&amp;timeout=false&amp;tempFileId=3023f6pdsm3oy9erj24ba97mtrk2rtbpdvp002.xlsx%3b%c2%a78.2+Pre-Survey+-+Question+Details.xlsx">Export finished</a>
</div></li>
<li class="d2l-datalist-item"><div class="d2l-datalist-item-content">
  <a class="d2l-link" href="/d2l/lms/quizzing/reports/quiz_report_export_summary.d2l?ou=611388&amp;success=false&amp;timeout=true&amp;tempFileId=abc.xlsx%3bX+-+User+Attempts.xlsx">Export failed</a>
</div></li>
<li class="d2l-datalist-item"><a class="d2l-link" href="/d2l/le/news/611388/1">An announcement</a></li>
</ul>
"""


def test_report_export_url_mirrors_view_report_defaults():
    url = quizzes.report_export_url(BASE, "611388", "519757", "10178", "1")
    assert url.startswith(f"{BASE}d2l/lms/quizzing/reports/quiz_report_export.d2l?")
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    assert query["qi"] == ["519757"] and query["ri"] == ["10178"] and query["rt"] == ["1"]
    assert query["excel"] == ["True"] and query["calcType"] == ["4"] and query["sd"] == [""]
    csv_all_url = quizzes.report_export_url(BASE, "1", "2", "3", "4", excel=False, all_attempts=True)
    csv_all = parse_qs(urlparse(csv_all_url).query)
    assert csv_all["excel"] == ["False"] and csv_all["calcType"] == ["3"]


def test_parse_quiz_list_flattens_pages():
    pages = [
        {"Objects": [{"QuizId": 519720, "Name": "§5.5 Pre-Quiz", "IsActive": True,
                      "StartDate": "2026-09-02T04:01:00.000Z", "DueDate": "2026-09-08T12:00:00.000Z",
                      "EndDate": None}], "Next": "https://example/next"},
        {"Objects": [{"QuizId": 519721, "Name": "§5.5 Pre-Survey"}], "Next": None},
    ]
    result = quizzes.parse_quiz_list(pages)
    assert [q["id"] for q in result] == ["519720", "519721"]
    assert result[0]["due"] == "2026-09-08T12:00:00.000Z" and result[0]["end"] is None
    assert result[1]["due"] is None


def test_parse_report_list_reads_ids_names_and_types():
    assert quizzes.parse_report_list(REPORT_LIST_HTML) == [
        {"id": "10178", "name": "Question Details including text responses", "type": "Question Details"},
        {"id": "10179", "name": "User Attempts", "type": "User Attempts"},
    ]
    assert quizzes.parse_report_list("<p>No reports</p>") == []


def test_parse_report_type_id():
    html = '<form><input type="hidden" name="rt" value="1"><input type="hidden" name="optionData" value="0"></form>'
    assert quizzes.parse_report_type_id(html) == "1"
    assert quizzes.parse_report_type_id("<form></form>") is None


def test_parse_export_alerts_strips_xssi_and_decodes_filenames():
    text = "while(1);" + json.dumps({"Payload": {"Html": ALERT_HTML}})
    alerts = quizzes.parse_export_alerts(text)
    assert len(alerts) == 2
    assert alerts[0]["temp_file_id"] == (
        "3023f6pdsm3oy9erj24ba97mtrk2rtbpdvp002.xlsx;§8.2 Pre-Survey - Question Details.xlsx"
    )
    assert alerts[0]["filename"] == "§8.2 Pre-Survey - Question Details.xlsx"
    assert alerts[0]["success"] is True
    assert alerts[1]["success"] is False


def test_parse_export_file_url():
    html = '<p>Click <a href="/d2l/common/viewFile.d2lfile/Temp/1/x.xlsx?ou=611388&amp;fid=MzA">here</a></p>'
    assert quizzes.parse_export_file_url(html) == "/d2l/common/viewFile.d2lfile/Temp/1/x.xlsx?ou=611388&fid=MzA"
    assert quizzes.parse_export_file_url("<p>Export failed</p>") is None


def test_export_and_safe_filenames():
    assert quizzes.export_filename("§8.2 Pre-Survey", "User Attempts") == "§8.2 Pre-Survey - User Attempts.xlsx"
    assert quizzes.export_filename("Q", "User Attempts", excel=False).endswith(".csv")
    assert quizzes.safe_filename("Ch 5/6: Review?.xlsx") == "Ch 5_6_ Review_.xlsx"


def test_report_matches_name_or_type():
    report = {"name": "Question Details including text responses", "type": "Question Details"}
    assert quizzes.report_matches(report, [])
    assert quizzes.report_matches(report, ["question details"])
    assert quizzes.report_matches(report, ["Question Details including text responses"])
    assert not quizzes.report_matches(report, ["User Attempts"])
