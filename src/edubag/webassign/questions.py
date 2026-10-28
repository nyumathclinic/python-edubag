"""Parse WebAssign assignment and question editor pages.

These pages carry the source of an assignment and its questions:

* My Classes / class overview (``faculty.pl?action=course/overview``): a
  table per class, one row per assignment. Each row's ``edit`` link sets
  ``aid`` (the assignment ID), its ``view`` link opens
  ``Assignment-Preview/deployment?id=…`` and its ``scores`` link sets
  ``clicked`` to ``"course,section"``.
* Assignment Editor (``faculty.pl?action=assignments/edit&aid=…``): the name,
  instructions and keywords are form fields; the question list and the
  settings are URL-escaped JSON passed to ``Assignment.Editor`` in a script.
* Question Editor (``faculty.pl?action=questions/edit&qid=…``): the name,
  mode and the Question, Answer and Solution source are form fields; the
  "Additional Information" table gives the textbook, author and so on.

The question source (``<eqn>`` Perl, ``<watex>``, ``<_>`` answer blanks, …)
is kept verbatim. Parse with ``html.parser``: it reads ``<textarea>`` content
as text, as a browser does, while lxml turns the markup in it into elements.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

# Question modes offered by the Question Editor. A multi-mode question's mode
# is the string of its parts' modes, e.g. "CQ".
MODES = {
    "C": "Multiple-Choice",
    "S": "Multiple-Select",
    "N": "Numerical",
    "B": "Fill-in-the-Blank",
    "M": "Matching",
    "E": "Essay",
    "Q": "Symbolic",
    "A": "Algebraic",
    "R": "NumberLine",
    "I": "Image Map",
    "G": "Graphing",
    "F": "File-Upload",
    "J": "Java",
    "P": "Poll",
}

CONTENT_RE = re.compile(r"Editor\.Content\(\s*unescape\(\s*\"([^\"]*)\"")
SETTINGS_RE = re.compile(r"initializeSettings\(\s*unescape\(\s*\"([^\"]*)\"")
AID_RE = re.compile(r"aid\.value='(\d+)'")
DEPLOYMENT_RE = re.compile(r"deployment(?:\?id=|\.value=')(\d+)")
CLICKED_RE = re.compile(r"clicked\.value='(\d+),(\d+)'")
USER_PASS_RE = re.compile(r"UserPass=([0-9a-f]{16,})")
EDITOR_HEADING_RE = re.compile(r"Editing\s+(.*?)\s*\((\d+)\)\s*$", re.S)
IMAGE_RE = re.compile(r"""["'=(]\s*((?:https?://[^"'\s<>()]+)?/[^"'\s<>()$]+\.(?:gif|png|jpe?g|svg))\b""", re.I)

# Controls that are page plumbing, session secrets or the live preview's answer boxes.
SKIP_FIELDS = {
    "CSRFToken", "UserPass", "flash_message", "action", "subaction", "gbaction", "clicked",
    "classDate", "course", "coursePeriod", "jumpto_course", "quick_id", "quick_format",
    "clearSearch", "mySearch", "rm", "quick", "qAction", "qID", "qFormat", "mybox",
    "searchfor", "format2", "qid", "from", "type", "page", "redisplay", "showPopup",
    "scols", "srows", "qcols", "qrows", "acols", "arows",
}
PREVIEW_FIELD_RE = re.compile(r"^(enabled_)?R[A-Z]_\d")


def _text(element) -> str:
    return " ".join(element.get_text(" ", strip=True).split()) if element else ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _control_value(element) -> str | None:
    """A form control's current value, as the browser would submit it."""
    if element.name == "textarea":
        # As a browser does: normalize line breaks and drop a newline right after <textarea>.
        text = element.get_text().replace("\r\n", "\n").replace("\r", "\n")
        return text.removeprefix("\n")
    if element.name == "select":
        option = element.find("option", selected=True) or element.find("option")
        return option.get("value", option.get_text()) if option else None
    if element.get("type") in ("checkbox", "radio") and not element.has_attr("checked"):
        return None
    return element.get("value", "")


def user_pass(text: str) -> str | None:
    """The session's ``UserPass`` from a WebAssign URL or page."""
    match = USER_PASS_RE.search(text)
    return match.group(1) if match else None


def redact(text: str, secret: str | None) -> str:
    """Remove the session's ``UserPass`` from text that will be saved."""
    return text.replace(secret, "REDACTED") if secret else text


def mode_names(mode: str) -> list[str]:
    """Names of a question's part modes, e.g. ``"CQ"`` -> Multiple-Choice, Symbolic."""
    if mode in MODES:
        return [MODES[mode]]
    return [MODES.get(letter, letter) for letter in mode]


def image_paths(*texts: str) -> list[str]:
    """Image paths named literally in question source (computed paths are skipped)."""
    found = []
    for text in texts:
        for path in IMAGE_RE.findall(text or ""):
            if path not in found:
                found.append(path)
    return found


def parse_assignment_list(html: str) -> list[dict[str, str]]:
    """Assignments listed on My Classes or a class overview page.

    Returns:
        list[dict[str, str]]: One dict per assignment with keys ``aid``,
        ``name``, ``deployment_id``, ``course_id``, ``section_id`` and ``due``
        (as displayed, e.g. "10-23-26 08:00 AM EDT").
    """
    soup = BeautifulSoup(html, "html.parser")
    assignments, seen = [], set()
    for link in soup.select('a[href*="aid.value"]'):
        # The name and "edit" links both set aid; one row per assignment per class.
        row = link.find_parent("tr")
        if row is None or id(row) in seen:
            continue
        seen.add(id(row))
        aid = AID_RE.search(link["href"]).group(1)
        row_html = str(row)
        deployment = DEPLOYMENT_RE.search(row_html)
        clicked = CLICKED_RE.search(row_html)
        cells = row.find_all("td", recursive=False)
        assignments.append({
            "aid": aid,
            "name": _text(link),
            "deployment_id": deployment.group(1) if deployment else "",
            "course_id": clicked.group(1) if clicked else "",
            "section_id": clicked.group(2) if clicked else "",
            "due": _text(cells[-1]) if len(cells) > 1 else "",
        })
    return assignments


def _question_entries(items: list, pool: str | None = None) -> list[dict]:
    """The questions in an assignment's content list, in order, including those in pools."""
    entries = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "question" and item.get("id"):
            parts = [
                {"name": part.get("name"), "mode": part.get("mode"), "points": part.get("points")}
                for part in item.get("items", [])
            ]
            textbook = item.get("textbook") or {}
            entries.append({
                "position": len(entries) + 1,
                "qid": str(item["id"]),
                "code": item.get("code", ""),
                "mode": item.get("mode", ""),
                "mode_names": mode_names(item.get("mode", "")),
                "parts": parts,
                "points": sum(float(p["points"]) for p in parts if p["points"] not in (None, "")),
                "textbook": textbook.get("textbook_name"),
                "chapter": item.get("chapter"),
                "pool": pool,
            })
        else:
            for value in item.values():
                if isinstance(value, list):
                    entries.extend(_question_entries(value, pool=str(item.get("id", ""))))
    for position, entry in enumerate(entries, start=1):
        entry["position"] = position
    return entries


@dataclass
class WebAssignAssignment:
    """An assignment's content as shown in the Assignment Editor."""

    aid: str
    name: str
    instructions: str = ""
    keywords: str = ""
    author: str = ""
    last_saved: str = ""
    settings: dict = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)
    contents: list = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    source_url: str = ""
    exported_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, path: Path) -> Path:
        return _write_json(path, self.to_dict())


def parse_assignment_editor(html: str) -> WebAssignAssignment:
    """Parse an Assignment Editor page.

    Raises:
        ValueError: If the page has no assignment content (e.g. not an Assignment Editor page).
    """
    content = CONTENT_RE.search(html)
    if content is None:
        raise ValueError("No assignment content found; is this an Assignment Editor page?")
    contents = json.loads(unquote(content.group(1)))
    # Each question's view link carries the session's UserPass.
    for item in contents:
        if isinstance(item, dict):
            item.pop("view_link", None)
    settings = SETTINGS_RE.search(html)

    soup = BeautifulSoup(html, "html.parser")

    def value(name: str) -> str:
        element = soup.find(attrs={"name": name})
        return (_control_value(element) or "") if element else ""

    heading = soup.find("h1")
    last_saved = re.search(r"Last Saved:\s*([^<\n]+?)\s*<", html)
    return WebAssignAssignment(
        aid=value("aid"),
        name=value("assignmentName") or _text(heading.find("b") if heading else None),
        instructions=value("heading"),
        keywords=value("keywords"),
        author=value("author"),
        last_saved=last_saved.group(1) if last_saved else "",
        settings=json.loads(unquote(settings.group(1))) if settings else {},
        questions=_question_entries(contents),
        contents=contents,
    )


@dataclass
class WebAssignQuestion:
    """A question's source as shown in the Question Editor."""

    qid: str
    name: str
    mode: str
    mode_names: list[str]
    question: str
    answer: str
    solution: str
    last_saved: str = ""
    info: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    images: list[dict] = field(default_factory=list)
    source_url: str = ""
    exported_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, path: Path) -> Path:
        return _write_json(path, self.to_dict())


def parse_question_editor(html: str) -> WebAssignQuestion:
    """Parse a Question Editor page.

    ``images`` lists the image paths named in the source as ``{"src": …}``;
    the client adds the downloaded ``file``.

    Raises:
        ValueError: If the page has no question source (e.g. not a Question Editor page).
    """
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("textarea", attrs={"name": "question"}) is None:
        raise ValueError("No question source found; is this a Question Editor page?")

    controls: dict[str, str] = {}
    for element in soup.find_all(["input", "select", "textarea"]):
        name = element.get("name")
        if not name or name in SKIP_FIELDS or PREVIEW_FIELD_RE.match(name):
            continue
        current = _control_value(element)
        if current is not None and name not in controls:
            controls[name] = current

    heading = EDITOR_HEADING_RE.search(_text(soup.find("h1")))
    info = {}
    for row in soup.select(".qe-additional-info .qe-editor-div-row"):
        cells = row.find_all("div", recursive=False)
        if len(cells) >= 2 and _text(cells[0]):
            info[_text(cells[0])] = _text(cells[1])
    last_saved = soup.select_one("a.qe-target-link")

    mode = controls.pop("mode", "")
    question, answer, solution = (controls.pop(key, "") for key in ("question", "answer", "solution"))
    name = controls.pop("code", "") or (heading.group(1) if heading else "")
    return WebAssignQuestion(
        qid=heading.group(2) if heading else "",
        name=name,
        mode=mode,
        mode_names=mode_names(mode),
        question=question,
        answer=answer,
        solution=solution,
        last_saved=_text(last_saved),
        info=info,
        fields=controls,
        images=[{"src": src} for src in image_paths(question, answer, solution)],
    )
