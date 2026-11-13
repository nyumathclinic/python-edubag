"""Page knowledge for Brightspace's Import Grades wizard and grade item descriptions.

Import Grades (Grades -> Enter Grades -> Import) is a three-step wizard:

1. ``file_select.d2l``: choose a CSV (uploaded first to the temporary-file
   endpoint ``/d2l/common/upload/uploadFile.d2l``, with progress RPCs) and
   press Continue;
2. ``errors_view.d2l``: errors and warnings found in the file (an empty grid
   when there are none); Continue goes on through an ``errors_view.d2lfile``
   RPC;
3. ``preview.d2l``: each student's grades as ``old -> new``; only its Import
   button writes grades, after which Brightspace redirects to Enter Grades
   (``user_list_view.d2l?feedback=6``).

Descriptions are read and written through the Valence grade object API.
"""

import html
import re
from urllib.parse import urlparse

from edubag.brightspace.manage_grades import is_read_only_request
from edubag.brightspace.quizzes import LE_API_VERSION

IMPORT_PATH = "d2l/lms/grades/admin/importexport/import/"

# Requests the wizard needs to reach the preview, without importing anything.
_UPLOAD_PATHS = (
    "/d2l/common/upload/uploadFile.d2l",
    "/d2l/common/rpc/progress/progress.d2lfile",
)
_PREVIEW_STEPS = ("file_select.d2l", "errors_view.d2l", "errors_view.d2lfile")
# The preview page's Import button posts back to the preview page.
_IMPORT_STEPS = ("preview.d2l", "preview.d2lfile")


def file_select_url(base_url: str, course: str) -> str:
    """URL of step 1 of Import Grades for a course org unit ID."""
    return f"{base_url}{IMPORT_PATH}file_select.d2l?ou={course}"


def grade_objects_url(base_url: str, course: str, object_id: int | None = None) -> str:
    """Valence API URL of a course's grade objects, or of one of them."""
    url = f"{base_url}d2l/api/le/{LE_API_VERSION}/{course}/grades/"
    return url if object_id is None else f"{url}{object_id}"


def _import_step(path: str) -> str | None:
    """The wizard page a path belongs to (lower case), if it is one.

    Brightspace spells the folder both ``importexport`` and ``importExport``.
    """
    lowered = path.lower()
    prefix = "/" + IMPORT_PATH
    return lowered[len(prefix):] if lowered.startswith(prefix) else None


def is_import_request(method: str, url: str, allow_import: bool = False) -> bool:
    """Whether the import flow may send this request.

    Allows read-only requests (see ``manage_grades.is_read_only_request``),
    the temporary file upload, and the wizard's steps up to the preview. The
    preview page's own POSTs, which carry out the import, are allowed only
    with ``allow_import``. Everything else, including Enter Grades, is blocked.
    """
    if is_read_only_request(method, url):
        return True
    path = urlparse(url).path
    if path in _UPLOAD_PATHS:
        return True
    step = _import_step(path)
    if step in _PREVIEW_STEPS:
        return True
    return allow_import and step in _IMPORT_STEPS


def is_import_done_redirect(url: str) -> bool:
    """Whether a URL is where Brightspace sends the browser after an import.

    It is Enter Grades with a ``feedback`` code, e.g.
    ``.../grades/admin/enter/user_list_view.d2l?feedback=6&ou=609453``.
    The guard still blocks it; only the request is awaited.
    """
    parsed = urlparse(url)
    return parsed.path.endswith("/grades/admin/enter/user_list_view.d2l") and "feedback=" in parsed.query


# Step 2's grid of errors and warnings: one list of cell texts per row, with
# the column headers first. Empty when the file has no problems.
EXTRACT_ERRORS_JS = r"""() => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const table = document.querySelector("table[summary='List of errors and warnings']");
  if (!table) return null;
  return [...table.querySelectorAll("tr")]
    .map((tr) => [...tr.children].map((c) => clean(c.innerText || c.textContent)))
    .filter((cells) => cells.some(Boolean));
}"""

# Step 3's preview grid: grade item names, then one list of cell texts per
# student (name first). Cells read "old / max -> new / max" when a grade changes.
EXTRACT_PREVIEW_JS = r"""() => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const table = document.querySelector("table[summary^='List of grade items']");
  if (!table) return null;
  const items = [...table.querySelectorAll("d2l-table-col-sort-button[data-fieldname][nosort]")]
    .map((b) => clean(b.textContent));
  const rows = [...table.querySelectorAll("tr:not([header])")]
    .map((tr) => [...tr.children].map((c) => clean(c.innerText || c.textContent)));
  return { items, rows };
}"""


def summarize_preview(preview: dict) -> dict:
    """Students, and changed grades per item, from ``EXTRACT_PREVIEW_JS`` output.

    No student names are kept. Brightspace stores fewer decimals than a CSV
    may carry, so re-importing an imported value shows ``94.444 / 100 ->
    94.444 / 100``; such a cell is not counted as a change.
    """
    items = preview.get("items") or []
    rows = [r for r in preview.get("rows") or [] if len(r) == len(items) + 1]
    changed = {item: 0 for item in items}
    for row in rows:
        for item, cell in zip(items, row[1:]):
            old, arrow, new = cell.partition("->")
            if arrow and old.strip() != new.strip():
                changed[item] += 1
    return {"students": len(rows), "items": items, "changed": changed}


# ----- Description stamps ------------------------------------------------------

_PARAGRAPH = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", fragment))).replace("\xa0", " ").strip()


def strip_stamp(description: str, marker: str) -> str:
    """``description`` without the paragraphs whose text starts with ``marker``."""
    kept = _PARAGRAPH.sub(
        lambda m: "" if _text(m.group(1)).startswith(marker) else m.group(0), description
    )
    return kept.rstrip()


def apply_stamp(description: str, stamp: str, marker: str) -> str:
    """Replace any previous stamp paragraph in ``description`` with ``stamp``.

    The stamp goes last. Its text must start with ``marker``, which is how it
    is found again on the next run; the rest of the description is kept as is.
    """
    if not _text(stamp).startswith(marker):
        raise ValueError(f"Stamp text must start with {marker!r}: {stamp!r}")
    body = strip_stamp(description or "", marker)
    return f"{body}\n{stamp}" if body else stamp


def same_html(a: str, b: str) -> bool:
    """Whether two descriptions match apart from whitespace between tags."""
    norm = lambda s: re.sub(r">\s+<", "><", re.sub(r"\s+", " ", s or "")).strip()  # noqa: E731
    return norm(a) == norm(b)


# Fields of a Valence GradeObject that a PUT accepts (the GET adds read-only ones).
_PUT_FIELDS = (
    "Name", "ShortName", "GradeType", "CategoryId", "Description", "Weight",
    "MaxPoints", "CanExceedMaxPoints", "IsBonus", "ExcludeFromFinalGradeCalculation",
    "GradeSchemeId", "AssociatedTool", "IsHidden",
)


def grade_object_update(grade_object: dict, description_html: str) -> dict:
    """PUT body for ``grade_object`` with a new HTML description, all else unchanged."""
    body = {k: grade_object[k] for k in _PUT_FIELDS if k in grade_object}
    body["Description"] = {"Content": description_html, "Type": "Html"}
    return body


def description_html(grade_object: dict) -> str:
    """The HTML of a Valence grade object's description ('' when unset)."""
    description = grade_object.get("Description") or {}
    return description.get("Html") or html.escape(description.get("Text") or "")
