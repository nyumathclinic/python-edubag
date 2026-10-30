"""Set up a multi-version Gradescope exam or quiz from a YAML file.

For each version this creates an Exam/Quiz assignment from the version's
template PDF, saves its outline and imports its rubric; then it groups the
versions into a version set titled after the exam. Each step is skipped if it
is already done, so a run that stops partway can be repeated.

The config file names the course, the exam's title and, for each version, its
files (paths are relative to the config file)::

    course: 1234567
    title: Quiz 6
    outline: outline-q06.json          # default for versions that name none
    versions:
      "0930": {pdf: q06-0930.qns.pdf, rubric: rubric-q06-0930.json}
      "1100": {pdf: q06-1100.qns.pdf, rubric: rubric-q06-1100.json}

Each version's assignment is titled ``title_format`` (default
``"{title} - {version}"``), unless the version names one with ``assignment``
(an ID or a title).

A version that names no outline or rubric uses the files the LaTeX package
exam-gradescope writes next to its PDF, if they are there: for
``q06-0930.qns.pdf``, ``q06-0930.qns.outline.json`` and
``q06-0930.qns.rubric.json``. With that package a config needs only::

    course: 1234567
    title: Quiz 6
    versions:
      "0930": {pdf: q06-0930.qns.pdf}
      "1100": {pdf: q06-1100.qns.pdf}

An outline file is the body Gradescope's outline editor sends, without the
``assignment`` wrapper. Coordinates are percentages of the page, measured from
its top left corner::

    {"identification_regions": {
       "name": {"page_number": 1, "x1": 10, "x2": 40, "y1": 5, "y2": 15},
       "sid":  {"page_number": 1, "x1": 60, "x2": 90, "y1": 5, "y2": 15}},
     "question_data": [
       {"title": "Q1", "weight": 2,
        "crop_rect_list": [{"page_number": 1, "x1": 10, "x2": 90, "y1": 10, "y2": 30}]}]}

Rubric files use the assignment format of the gradescope-tampermonkey rubric
importer (``gradescope/rubric-import.user.js`` in
https://github.com/nyumathclinic/gradescope-tampermonkey): ``{"questions":
[{"question": "2", "title": ..., "scoring_type": ..., "floor": ..., "ceiling":
..., "rubric_items": [...], "rubric_item_groups": [...]}]}``. The parsing and
the requests here follow that script, which records the endpoints Gradescope's
own rubric editor was seen using. The outline and version set endpoints are
the ones Gradescope's own pages were recorded sending.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import yaml
from loguru import logger
from playwright.sync_api import Page

SETTINGS_KEYS = ("scoring_type", "floor", "ceiling")
SCORING_TYPES = ("positive", "negative")
DEFAULT_TITLE_FORMAT = "{title} - {version}"
RECT_KEYS = ("page_number", "x1", "x2", "y1", "y2")


@dataclass
class VersionConfig:
    """One version of an exam: its files and the assignment that holds it."""

    name: str
    assignment: str
    pdf: Path | None = None
    outline: Path | None = None
    rubric: Path | None = None


@dataclass
class ExamConfig:
    """A multi-version exam, read from a YAML file with ``load_exam_config``."""

    course: str
    title: str
    versions: list[VersionConfig] = field(default_factory=list)


def load_exam_config(path: Path) -> ExamConfig:
    """Read and check an exam config file and the outline and rubric files it names.

    Raises:
        ValueError: If a required key is missing, a named file does not exist,
            an outline is malformed, or a rubric names a question its
            version's outline does not have.
    """
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    errors = []
    for key in ("course", "title", "versions"):
        if key not in data:
            errors.append(f"missing '{key}'")
    if not isinstance(data.get("versions", {}), dict) or not data.get("versions"):
        errors.append("'versions' must map each version's name to its files")
    if errors:
        raise ValueError(f"{path}: " + "; ".join(errors))

    title = str(data["title"])
    title_format = str(data.get("title_format", DEFAULT_TITLE_FORMAT))
    versions = []
    for name, entry in data["versions"].items():
        name = str(name)
        entry = {"outline": data.get("outline")} | (entry or {})
        files = {}
        for key in ("pdf", "outline", "rubric"):
            if entry.get(key) is None:
                files[key] = None
                continue
            file = (path.parent / entry[key]).resolve()
            if not file.is_file():
                errors.append(f"version {name}: {key} file not found: {file}")
                file = None
            files[key] = file
        # Unless the config names them, use the files the LaTeX package
        # exam-gradescope writes next to the PDF: q06-0930.qns.outline.json.
        if files["pdf"]:
            for key in ("outline", "rubric"):
                generated = files["pdf"].with_suffix(f".{key}.json")
                if entry.get(key) is None and generated.is_file():
                    files[key] = generated
        if files["outline"]:
            outline = json.loads(files["outline"].read_text())
            errors.extend(f"version {name}: {e}" for e in check_outline(outline))
            if files["rubric"]:
                indexes = set(outline_indexes(outline.get("question_data") or []))
                rubric = json.loads(files["rubric"].read_text())
                for q in rubric.get("questions") or []:
                    if str(q.get("question")) not in indexes:
                        errors.append(
                            f"version {name}: the rubric's question {q.get('question')} is not in the outline"
                        )
        assignment = str(entry.get("assignment") or title_format.format(title=title, version=name))
        versions.append(VersionConfig(name=name, assignment=assignment, **files))
    if errors:
        raise ValueError(f"{path}: " + "; ".join(errors))
    return ExamConfig(course=str(data["course"]), title=title, versions=versions)


# ---------------------------------------------------------------------------
# Outline files
# ---------------------------------------------------------------------------


def check_outline(outline: Any) -> list[str]:
    """Problems with an outline file's contents (see the module docstring)."""
    if not isinstance(outline, dict):
        return ["outline: expected an object"]
    errors = []

    def check_rect(rect: Any, where: str) -> None:
        if not isinstance(rect, dict) or any(not _is_number(rect.get(k)) for k in RECT_KEYS):
            errors.append(f"{where}: expected numbers {', '.join(RECT_KEYS)}")
            return
        if not (0 <= rect["x1"] < rect["x2"] <= 100 and 0 <= rect["y1"] < rect["y2"] <= 100):
            errors.append(f"{where}: expected 0 <= x1 < x2 <= 100 and 0 <= y1 < y2 <= 100")

    regions = outline.get("identification_regions") or {}
    for key in ("name", "sid"):
        if key in regions:
            check_rect(regions[key], f"outline: identification_regions.{key}")

    def check_questions(questions: Any, where: str) -> None:
        if not isinstance(questions, list) or not questions:
            errors.append(f"{where}: expected a non-empty array")
            return
        for i, q in enumerate(questions):
            here = f"{where}[{i}]"
            if not isinstance(q, dict):
                errors.append(f"{here}: expected an object")
                continue
            if q.get("children"):
                check_questions(q["children"], f"{here}.children")
                continue
            if not _is_number(q.get("weight")):
                errors.append(f"{here}.weight: expected a number")
            rects = q.get("crop_rect_list")
            if not isinstance(rects, list) or not rects:
                errors.append(f"{here}.crop_rect_list: expected a non-empty array")
                continue
            for j, rect in enumerate(rects):
                check_rect(rect, f"{here}.crop_rect_list[{j}]")

    check_questions(outline.get("question_data"), "outline: question_data")
    return errors


def outline_from_page(props: dict) -> dict:
    """An outline file's contents (see the module docstring) from AssignmentOutline page props.

    The page lists saved questions flat, each with its ``parent_id`` and its
    ``index`` among its siblings; this nests them again.
    """

    def rect(r: dict) -> dict:
        return {k: r[k] for k in RECT_KEYS}

    def children_of(parent: Any) -> list[dict]:
        siblings = sorted((q for q in props.get("outline") or [] if q.get("parent_id") == parent),
                          key=lambda q: q.get("index") or 0)
        out = []
        for q in siblings:
            entry: dict = {"title": q.get("title") or ""}
            if children := children_of(q["id"]):
                entry["children"] = children
            else:
                entry["weight"] = float(q["weight"])
                entry["crop_rect_list"] = [rect(r) for r in q.get("crop_rect_list") or []]
            out.append(entry)
        return out

    regions = (props.get("assignment") or {}).get("id_regions") or {}
    return {
        "identification_regions": {k: rect(regions[k]) for k in ("name", "sid") if regions.get(k)},
        "question_data": children_of(None),
    }


def outline_indexes(questions: list[dict], prefix: str = "") -> list[str]:
    """The numbers Gradescope gives an outline's gradable questions: "1", "2.1", ..."""
    indexes = []
    for i, q in enumerate(questions, start=1):
        index = f"{prefix}{i}"
        indexes.extend(outline_indexes(q["children"], f"{index}.") if q.get("children") else [index])
    return indexes


# ---------------------------------------------------------------------------
# Rubric files
# ---------------------------------------------------------------------------


def _by_position(entries: list) -> list:
    """Sort by ``position`` if every entry has one; otherwise keep file order."""
    if all(isinstance(e, dict) and isinstance(e.get("position"), (int, float)) for e in entries):
        return sorted(entries, key=lambda e: e["position"])
    return list(entries)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None or value == "":
        return False
    try:
        return float(value) == float(value)  # rejects NaN
    except (TypeError, ValueError):
        return False


def normalize_rubric(data: Any) -> tuple[dict, list[str]]:
    """Parse one question's rubric.

    Accepts items nested inside their group, or Gradescope's flat form (items
    carrying a ``group_id``).

    Returns:
        ``(rubric, errors)``. ``rubric`` has ``settings`` (the keys of
        ``SETTINGS_KEYS`` the file gives) and ``entries``, the top-level
        order: ``{"item": {...}}`` or ``{"group": {"description",
        "mutually_exclusive", "items"}}``.
    """
    errors: list[str] = []
    rubric: dict = {"settings": {}, "entries": []}
    if not isinstance(data, dict):
        return rubric, ["top level: expected an object"]

    if "scoring_type" in data:
        if data["scoring_type"] in SCORING_TYPES:
            rubric["settings"]["scoring_type"] = data["scoring_type"]
        else:
            errors.append(f"scoring_type: expected one of {', '.join(SCORING_TYPES)}")
    for key in ("floor", "ceiling"):
        if key in data:
            if isinstance(data[key], bool):
                rubric["settings"][key] = data[key]
            else:
                errors.append(f"{key}: expected true or false")

    def as_list(value: Any, where: str) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return _by_position(value)
        errors.append(f"{where}: expected an array")
        return []

    def item(raw: Any, where: str) -> dict | None:
        if not isinstance(raw, dict):
            errors.append(f"{where}: expected an object")
            return None
        if "rubric_items" in raw or "rubric_item_groups" in raw:
            errors.append(f"{where}: groups cannot be nested")
        description = raw.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{where}.description: expected non-empty text")
        if not _is_number(raw.get("weight")):
            errors.append(f"{where}.weight: expected a number")
            return None
        return {"description": description, "weight": float(raw["weight"])}

    top: list[tuple[Any, dict]] = []
    groups_by_id: dict[str, dict] = {}
    for i, raw in enumerate(as_list(data.get("rubric_item_groups"), "rubric_item_groups")):
        where = f"rubric_item_groups[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: expected an object")
            continue
        description = raw.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{where}.description: expected non-empty text")
        if "mutually_exclusive" in raw and not isinstance(raw["mutually_exclusive"], bool):
            errors.append(f"{where}.mutually_exclusive: expected true or false")
        items = [
            parsed
            for j, it in enumerate(as_list(raw.get("rubric_items"), f"{where}.rubric_items"))
            if (parsed := item(it, f"{where}.rubric_items[{j}]"))
        ]
        group = {
            "description": description,
            "mutually_exclusive": raw.get("mutually_exclusive") is True,
            "items": items,
        }
        top.append((raw.get("position"), {"group": group}))
        if raw.get("id") is not None:
            groups_by_id[str(raw["id"])] = group

    for i, raw in enumerate(as_list(data.get("rubric_items"), "rubric_items")):
        where = f"rubric_items[{i}]"
        parsed = item(raw, where)
        if parsed is None:
            continue
        group_id = raw.get("group_id")
        if group_id is None:
            top.append((raw.get("position"), {"item": parsed}))
        elif str(group_id) in groups_by_id:
            groups_by_id[str(group_id)]["items"].append(parsed)
        else:
            errors.append(f"{where}.group_id: no group with id {group_id}")

    # Ungrouped items and groups share one position sequence. Without a full
    # set of positions, items go first.
    if all(isinstance(p, (int, float)) for p, _ in top):
        top.sort(key=lambda entry: entry[0])
    else:
        top.sort(key=lambda entry: "group" in entry[1])
    rubric["entries"] = [entry for _, entry in top]

    for entry in rubric["entries"]:
        if "group" in entry and not entry["group"]["items"]:
            errors.append(f"group \"{entry['group']['description']}\": a group needs at least one item")
    return rubric, errors


def leaf_questions(questions: list[dict]) -> list[dict]:
    """The questions that can have a rubric: those with no subquestions."""
    leaves = []
    for q in questions or []:
        leaves.extend(leaf_questions(q["children"]) if q.get("children") else [q])
    return leaves


def match_rubric_file(data: Any, questions: list[dict]) -> tuple[list[tuple[dict, dict]], list[str], list[str]]:
    """Match an assignment rubric file's entries to an assignment's questions.

    Args:
        data: The parsed rubric file, ``{"questions": [...]}``.
        questions: The assignment's leaf questions, each with ``full_index``.

    Returns:
        ``(entries, errors, warnings)``, where ``entries`` pairs each question
        with its normalized rubric.
    """
    errors: list[str] = []
    warnings: list[str] = []
    entries: list[tuple[dict, dict]] = []
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return entries, ['top level: expected an object with a "questions" array'], warnings
    if not data["questions"]:
        errors.append("questions: the file has no questions")
    seen: set[str] = set()
    for i, raw in enumerate(data["questions"]):
        where = f"questions[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: expected an object")
            continue
        if isinstance(raw.get("question"), bool) or not isinstance(raw.get("question"), (str, int, float)):
            errors.append(f'{where}.question: expected a question number, e.g. "2.3"')
            continue
        index = str(raw["question"])
        question = next((q for q in questions if str(q.get("full_index")) == index), None)
        if question is None:
            errors.append(f"{where}: this assignment has no question {index} with a rubric")
            continue
        if index in seen:
            errors.append(f"{where}: question {index} appears more than once")
        seen.add(index)
        title = raw.get("title")
        if isinstance(title, str) and title.strip() != str(question.get("title") or "").strip():
            warnings.append(f"question {index} is titled \"{question.get('title')}\" here, \"{title}\" in the file")
        rubric, problems = normalize_rubric(raw)
        errors.extend(f"question {index}: {p}" for p in problems)
        entries.append((question, rubric))
    return entries, errors, warnings


def existing_rubric(question: dict, props: dict) -> dict:
    """A question's current rubric items and groups, from AssignmentRubric page props."""

    def mine(entry: dict) -> bool:
        return entry.get("question_id") in (None, question["id"])

    return {
        "rubric_items": [e for e in props.get("rubric_items") or [] if mine(e)],
        "rubric_item_groups": [e for e in props.get("rubric_item_groups") or [] if mine(e)],
    }


def same_rubric(existing: dict, rubric: dict) -> bool:
    """Whether a question's current rubric already has the file's items and groups.

    Compares descriptions, weights and grouping, ignoring order. Settings are
    not compared.
    """
    groups = {g["id"]: g for g in existing["rubric_item_groups"]}

    def have() -> list:
        out = []
        for it in existing["rubric_items"]:
            group = groups.get(it.get("group_id"))
            out.append((group["description"] if group else None, it["description"], float(it["weight"])))
        return sorted(out, key=repr)

    def want() -> list:
        out = []
        for entry in rubric["entries"]:
            if "item" in entry:
                out.append((None, entry["item"]["description"], entry["item"]["weight"]))
            else:
                g = entry["group"]
                out.extend((g["description"], it["description"], it["weight"]) for it in g["items"])
        return sorted(out, key=repr)

    return have() == want() and len(groups) == sum("group" in e for e in rubric["entries"])


def _weight(value: float) -> str:
    """A weight as Gradescope's rubric editor sends it (like JavaScript's String(number))."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _item_body(item: dict) -> dict:
    return {"description": item["description"], "weight": _weight(item["weight"])}


# ---------------------------------------------------------------------------
# Talking to Gradescope from a logged-in page
# ---------------------------------------------------------------------------


def send_json(
    page: Page, method: str, path: str, body: dict | None = None, form: list[tuple[str, str]] | None = None
) -> Any:
    """Send a request as Gradescope's own pages do, with the page's CSRF token.

    The body is ``body`` as JSON, or ``form`` (a list of pairs, so a key may
    repeat) URL-encoded. The response is parsed as JSON if it can be.

    Raises:
        RuntimeError: If the response is not a success.
    """
    csrf = page.locator('meta[name="csrf-token"]').first.get_attribute("content") or ""
    if form is not None:
        content_type, data = "application/x-www-form-urlencoded; charset=UTF-8", urlencode(form)
    else:
        content_type, data = "application/json", None if body is None else json.dumps(body)
    response = page.request.fetch(
        # Resolve against page.url: a request across the gradescope.com -> www redirect loses its method.
        urljoin(page.url, path),
        method=method,
        headers={
            "Content-Type": content_type,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": csrf,
        },
        data=data,
    )
    text = response.text()
    if not response.ok:
        raise RuntimeError(f"{method} {path} returned {response.status}: {text[:300]}")
    try:
        return json.loads(text)
    except ValueError:
        return None


def read_rubric_page(page: Page) -> dict:
    """The AssignmentRubric component's props on a loaded Create Rubric page."""
    holder = page.locator('[data-react-class="AssignmentRubric"]')
    if holder.count() == 0:
        raise RuntimeError(f"No rubric editor on {page.url}")
    return json.loads(holder.first.get_attribute("data-react-props") or "{}")


def assignments_titled(table_data: list[dict], title: str) -> list[str]:
    """IDs of the assignments with a title, from an Assignments page's ``table_data``.

    Titles are compared ignoring case and surrounding or repeated whitespace.
    """

    def normalize(text: str) -> str:
        return " ".join(str(text).split()).casefold()

    return [
        e["url"].rstrip("/").rsplit("/", 1)[-1]
        for e in table_data
        if e.get("type") == "assignment" and e.get("url") and normalize(e.get("title", "")) == normalize(title)
    ]


def container_of(table_data: list[dict], assignment: str) -> str | None:
    """The ID of the version set holding an assignment, or None."""
    for e in table_data:
        if e.get("type") == "assignment" and str(e.get("url", "")).rstrip("/").endswith(f"/{assignment}"):
            return None if e.get("container_id") is None else str(e["container_id"])
    return None


def create_exam_assignment(page: Page, base_url: str, course: str, title: str, pdf: Path) -> str:
    """Create an instructor-uploaded Exam/Quiz assignment from a template PDF.

    This fills in Gradescope's Create Assignment dialog, with rubrics made
    before submissions, so they can be imported right away.

    Returns:
        The new assignment's ID.
    """
    page.goto(f"{base_url}/courses/{course}/assignments")
    page.wait_for_load_state("networkidle")
    if "login" in page.url:
        raise RuntimeError("Authentication session expired. Please re-authenticate.")
    page.locator("a.js-newAssignment").click()
    page.locator("#examQuiz-tab").click()
    page.get_by_role("button", name="Next").click()
    page.locator("#new_assignment_title").fill(title)
    page.locator("#assignment_template_pdf").set_input_files(pdf)
    for radio in ("assignment_student_submission_false", "assignment_when_to_create_rubric_before_submissions"):
        # The radios are hidden behind their styled labels, which take the clicks.
        if not page.locator(f"#{radio}").is_checked():
            page.locator(f"label[for='{radio}']").click()
        if not page.locator(f"#{radio}").is_checked():
            raise RuntimeError(f"Could not select {radio} in the Create Assignment dialog")
    page.get_by_role("button", name="Create Assignment").click()
    # Gradescope opens the new assignment's outline editor.
    page.wait_for_url("**/assignments/*/outline/edit", timeout=120000)
    return page.url.split("/assignments/")[1].split("/")[0]


def read_outline_page(page: Page, base_url: str, course: str, assignment: str) -> dict:
    """The AssignmentOutline props from an assignment's outline editor.

    Includes ``outline`` (the saved questions, empty if none) and ``template``
    (its ``status_string`` is "processing" until Gradescope has read the PDF).
    """
    page.goto(f"{base_url}/courses/{course}/assignments/{assignment}/outline/edit")
    page.wait_for_load_state("domcontentloaded")
    if "login" in page.url:
        raise RuntimeError("Authentication session expired. Please re-authenticate.")
    holder = page.locator('[data-react-class="AssignmentOutline"]')
    if holder.count() == 0:
        raise RuntimeError(f"No outline editor on {page.url}")
    return json.loads(holder.first.get_attribute("data-react-props") or "{}")


def save_outline(
    page: Page, base_url: str, course: str, assignment: str, outline: dict, timeout: float = 180
) -> bool:
    """Save an outline (see the module docstring) unless the assignment has one.

    Waits for Gradescope to finish reading the template PDF first.

    Returns:
        True if the outline was saved, False if the assignment already had one.
    """
    deadline = time.monotonic() + timeout
    while True:
        props = read_outline_page(page, base_url, course, assignment)
        if props.get("outline"):
            return False
        if (props.get("template") or {}).get("status_string") != "processing":
            break
        if time.monotonic() > deadline:
            raise RuntimeError(f"Gradescope was still processing the template of assignment {assignment}")
        page.wait_for_timeout(3000)
    page_count = (props.get("template") or {}).get("page_count")
    if page_count:
        rects = [outline["identification_regions"][k] for k in ("name", "sid") if k in outline.get(
            "identification_regions", {})]
        stack = list(outline["question_data"])
        while stack:
            q = stack.pop()
            stack.extend(q.get("children") or [])
            rects.extend(q.get("crop_rect_list") or [])
        if any(r["page_number"] > page_count for r in rects):
            raise ValueError(f"the outline has a region past the template's last page ({page_count})")
    send_json(
        page,
        "PATCH",
        f"/courses/{course}/assignments/{assignment}/outline/",
        {"assignment": {"identification_regions": outline.get("identification_regions") or {}},
         "question_data": outline["question_data"]},
    )
    return True


def create_version_set(page: Page, course: str, title: str, assignments: list[str]) -> None:
    """Group assignments into a version set, as the Assignments page's "Create Version Set" does."""
    send_json(
        page,
        "POST",
        f"/courses/{course}/assignment_containers",
        form=[("title", title)] + [("assignment_ids[]", a) for a in assignments],
    )


def import_question_rubric(
    page: Page,
    course: str,
    question: dict,
    rubric: dict,
    existing: dict | None,
    dry_run: bool = False,
) -> list[str]:
    """Replace one question's rubric with ``rubric`` (from ``normalize_rubric``).

    ``existing`` (from ``existing_rubric``) is deleted first, if given. A
    setting the file leaves out keeps the question's current value.

    Returns:
        A line for each change, made or (in a dry run) planned.
    """
    base = f"/courses/{course}/questions/{question['id']}"
    done: list[str] = []

    def step(label: str, method: str, path: str, body: dict | None = None) -> Any:
        if dry_run:
            done.append(f"would {label}")
            return None
        result = send_json(page, method, path, body)
        done.append(label)
        return result

    if existing:
        for it in existing["rubric_items"]:
            step(f"delete item \"{it['description']}\"", "DELETE", f"{base}/rubric_items/{it['id']}")
        for g in existing["rubric_item_groups"]:
            step(f"delete group \"{g['description']}\"", "DELETE", f"{base}/rubric_item_groups/{g['id']}")

    if rubric["settings"]:
        settings = {key: question.get(key) for key in SETTINGS_KEYS} | rubric["settings"]
        missing = [key for key in SETTINGS_KEYS if settings[key] is None]
        if missing:
            raise RuntimeError(
                f"could not read question {question.get('full_index')}'s current {', '.join(missing)}; "
                f"give all of {', '.join(SETTINGS_KEYS)} in the file"
            )
        step(
            "update settings",
            "POST",
            f"{base}/save_grading_options",
            {
                "question": {
                    "id": question["id"],
                    "scoring_type": settings["scoring_type"],
                    "ceiling": "1" if settings["ceiling"] else "0",
                    "floor": "1" if settings["floor"] else "0",
                }
            },
        )

    for entry in rubric["entries"]:
        if "item" in entry:
            step(f"add item \"{entry['item']['description']}\"", "POST", f"{base}/rubric_items",
                 {"rubric_item": _item_body(entry["item"])})
            continue
        group = entry["group"]
        created = step(
            f"add group \"{group['description']}\" with {len(group['items'])} item(s)",
            "POST",
            f"{base}/rubric/create_group_with_items",
            {
                "rubric_item_group": {"description": group["description"]},
                "rubric_items": [_item_body(it) for it in group["items"]],
            },
        )
        if not group["mutually_exclusive"]:
            continue
        if dry_run:
            done.append("would set it to select one")
            continue
        group_id = ((created or {}).get("rubric_item_group") or {}).get("id")
        if group_id is None:
            raise RuntimeError(f"could not find the new group's id in the response for \"{group['description']}\"")
        if len((created or {}).get("rubric_items") or []) != len(group["items"]):
            raise RuntimeError(f"group \"{group['description']}\" was not created with all its items")
        step("set it to select one", "PUT", f"{base}/rubric_item_groups/{group_id}",
             {"id": group_id, "mutually_exclusive": True})
    return done


def import_assignment_rubric(
    page: Page,
    base_url: str,
    course: str,
    assignment: str,
    data: Any,
    replace: bool = False,
    dry_run: bool = False,
) -> dict:
    """Import a rubric file into every question it names.

    A question whose current rubric already matches the file is left alone, so
    a second run changes nothing. A question with a different rubric is
    replaced only if ``replace`` is set.

    Args:
        page: A logged-in page.
        base_url: Gradescope's base URL.
        course: Gradescope course ID.
        assignment: Gradescope assignment ID.
        data: The parsed rubric file.
        replace: Delete a question's differing rubric before importing.
        dry_run: Report what would change, but send nothing.

    Returns:
        dict: ``assignment``, ``warnings``, and ``questions``, a list of
            ``{"question", "status", "changes"}`` where status is
            ``"imported"``, ``"unchanged"`` or ``"conflict"``.

    Raises:
        ValueError: If the file does not fit the assignment's questions.
        RuntimeError: If a request fails; questions reported before it were done.
    """
    page.goto(f"{base_url}/courses/{course}/assignments/{assignment}/rubric/edit")
    page.wait_for_load_state("domcontentloaded")
    if "login" in page.url:
        raise RuntimeError("Authentication session expired. Please re-authenticate.")
    props = read_rubric_page(page)
    entries, errors, warnings = match_rubric_file(data, leaf_questions(props.get("questions") or []))
    if errors:
        raise ValueError("; ".join(errors))

    results = []
    for question, rubric in entries:
        label = f"question {question.get('full_index')}" + (f" ({question['title']})" if question.get("title") else "")
        current = existing_rubric(question, props)
        has_rubric = bool(current["rubric_items"] or current["rubric_item_groups"])
        if has_rubric and same_rubric(current, rubric):
            results.append({"question": label, "status": "unchanged", "changes": []})
            continue
        if has_rubric and not replace:
            results.append({"question": label, "status": "conflict", "changes": []})
            continue
        changes = import_question_rubric(page, course, question, rubric, current if has_rubric else None, dry_run)
        for change in changes:
            logger.info(f"assignment {assignment}, question {label}: {change}")
        results.append({"question": label, "status": "imported", "changes": changes})
    return {"assignment": assignment, "warnings": warnings, "questions": results}
