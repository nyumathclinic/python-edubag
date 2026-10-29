"""Page knowledge for downloading Brightspace quiz reports.

The quiz list comes from the Valence API. A report export, which in the UI
runs View Reports -> report -> Generate Excel -> bell notification -> dialog
-> download link, reduces to four GETs:

1. ``quiz_report_export.d2l`` queues the export (it is what the "Creating
   Report" popup loads in its hidden frame);
2. the bell's alert feed (``GetAlertsDaylight?Category=1``) gains an
   "Export finished" item linking to ``quiz_report_export_summary.d2l``,
   whose ``tempFileId`` ends with the export's filename;
3. that summary page links to the file under ``viewFile.d2lfile``;
4. which serves the file itself.
"""

import json
import re
from urllib.parse import parse_qs, unquote_plus, urlencode, urlparse

from bs4 import BeautifulSoup

LE_API_VERSION = "1.80"
REPORTS_PATH = "d2l/lms/quizzing/reports/"

# Valence responses and D2L's partial-rendering RPCs carry an XSSI guard.
_XSSI_PREFIX = "while(1);"


def quiz_list_url(base_url: str, course: str) -> str:
    """Valence API URL of the first page of a course's quizzes."""
    return f"{base_url}d2l/api/le/{LE_API_VERSION}/{course}/quizzes/"


def report_list_url(base_url: str, course: str, quiz_id: str) -> str:
    """URL of a quiz's "View Reports" page."""
    return f"{base_url}{REPORTS_PATH}quiz_reports.d2l?qi={quiz_id}&ou={course}"


def report_build_url(base_url: str, course: str, quiz_id: str, report_id: str) -> str:
    """URL of a report's "View Report" page (the one with Generate CSV/Excel)."""
    return f"{base_url}{REPORTS_PATH}quiz_report_build.d2l?isprv=&qi={quiz_id}&ri={report_id}&cfql=0&ou={course}"


def report_export_url(
    base_url: str,
    course: str,
    quiz_id: str,
    report_id: str,
    report_type_id: str,
    excel: bool = True,
    all_attempts: bool = False,
) -> str:
    """URL that queues a report export, with the View Report page's defaults.

    Mirrors ``ExportToCsv`` on the View Report page: whole course, no
    completion date range, exempted users included. ``calcType`` 4 is
    "First Attempts" (the page default) and 3 is "All Attempts".
    """
    query = {
        "ou": course,
        "qi": quiz_id,
        "ri": report_id,
        "rt": report_type_id,
        "od": 0,
        "outid": 0,
        "oupid": 0,
        "sd": "",
        "ed": "",
        "prcd": "False",
        "excel": str(excel),
        "ie": "True",
        "viewby": 0,
        "groupid": 0,
        "grouptypeid": 0,
        "calcType": 3 if all_attempts else 4,
        "d2l_body_type": 3,
    }
    return f"{base_url}{REPORTS_PATH}quiz_report_export.d2l?{urlencode(query)}"


def alerts_url(base_url: str, course: str) -> str:
    """URL of the bell's "Updates" alert feed, where export notifications land."""
    return f"{base_url}d2l/NavigationArea/{course}/ActivityFeed/GetAlertsDaylight?Category=1&isXhr=true"


def strip_xssi(text: str) -> str:
    """Drop D2L's ``while(1);`` guard from a JSON response."""
    return text[len(_XSSI_PREFIX):] if text.startswith(_XSSI_PREFIX) else text


def parse_quiz_list(pages: list[dict]) -> list[dict]:
    """Flatten Valence ``ObjectListPage`` responses into quiz summaries.

    Dates are kept as the API's UTC ISO strings (or None).
    """
    return [
        {
            "id": str(quiz["QuizId"]),
            "name": quiz["Name"],
            "is_active": quiz.get("IsActive"),
            "start": quiz.get("StartDate"),
            "due": quiz.get("DueDate"),
            "end": quiz.get("EndDate"),
        }
        for page in pages
        for quiz in page.get("Objects", [])
    ]


def parse_report_list(html: str) -> list[dict]:
    """Reports set up on a quiz, from its "View Reports" page.

    Each is ``{id, name, type}``; ``type`` (e.g. "Question Details") is what
    Brightspace uses in the exported filename.
    """
    soup = BeautifulSoup(html, "html.parser")
    reports = []
    for link in soup.select("a[href*='quiz_report_build.d2l']"):
        report_id = parse_qs(urlparse(link["href"]).query).get("ri", [None])[0]
        if report_id is None:
            continue
        report_type = None
        details = link.find_parent("tr").find_next_sibling("tr")
        if details is not None:
            for label in details.find_all("label"):
                if label.get_text(strip=True) == "Report Type:":
                    value = label.find_next_sibling("label")
                    report_type = value.get_text(strip=True) if value else None
        reports.append({"id": report_id, "name": link.get_text(strip=True), "type": report_type})
    return reports


def parse_report_type_id(html: str) -> str | None:
    """The numeric report type (``rt``) from a "View Report" page."""
    field = BeautifulSoup(html, "html.parser").find("input", attrs={"name": "rt"})
    return field.get("value") if field else None


def parse_export_alerts(text: str) -> list[dict]:
    """Quiz report export notifications in an alert feed response, newest first.

    Each is ``{temp_file_id, filename, success, href}``.
    """
    payload = json.loads(strip_xssi(text))
    soup = BeautifulSoup(payload.get("Payload", {}).get("Html", ""), "html.parser")
    alerts = []
    for link in soup.select("a[href*='quiz_report_export_summary.d2l']"):
        query = parse_qs(urlparse(link["href"]).query)
        temp_file_id = unquote_plus(query.get("tempFileId", [""])[0]) if "tempFileId" in query else ""
        alerts.append(
            {
                "temp_file_id": temp_file_id,
                "filename": temp_file_id.split(";", 1)[1] if ";" in temp_file_id else None,
                "success": query.get("success", ["false"])[0].lower() == "true",
                "href": link["href"],
            }
        )
    return alerts


def parse_export_file_url(html: str) -> str | None:
    """Link to the exported file on an export summary page."""
    link = BeautifulSoup(html, "html.parser").select_one("a[href*='viewFile.d2lfile']")
    return link["href"] if link else None


def export_filename(quiz_name: str, report_type: str, excel: bool = True) -> str:
    """The filename Brightspace gives an export, e.g. "§8.2 Pre-Survey - Question Details.xlsx"."""
    return f"{quiz_name} - {report_type}.{'xlsx' if excel else 'csv'}"


def safe_filename(name: str) -> str:
    """``name`` with path separators and other awkward characters replaced."""
    return re.sub(r'[/\\:*?"<>|]', "_", name).strip()


def report_matches(report: dict, wanted: list[str] | tuple[str, ...]) -> bool:
    """Whether a report is selected by name or type (case-insensitive); all are if ``wanted`` is empty."""
    if not wanted:
        return True
    keys = {(report.get("name") or "").casefold(), (report.get("type") or "").casefold()}
    return any(w.casefold() in keys for w in wanted)
