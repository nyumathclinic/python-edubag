"""Page knowledge for crawling Brightspace's Manage Grades area (read-only).

The crawl visits the Manage Grades list and each grade object's edit tabs
(Properties, Restrictions, Objectives) by direct GET, never through the tab
links: switching tabs in the UI POSTs the whole edit form back to the server.
"""

from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse

# Edit-page tabs by grade object kind, in the order Brightspace shows them.
EDIT_TABS: dict[str, dict[str, str]] = {
    "item": {
        "properties": "item_props_newedit.d2l",
        "restrictions": "item_rests_edit.d2l",
        "objectives": "item_activities_edit.d2l",
    },
    "category": {
        "properties": "category_props_newedit.d2l",
        "restrictions": "category_rests_edit.d2l",
    },
    "final_grade": {
        "properties": "finalgrade_props_edit.d2l",
    },
}

MANAGE_PATH = "d2l/lms/grades/admin/manage/"

# Localization lookups are POSTs but read-only; every other write is blocked.
_ALLOWED_POST_PATHS = ("/d2l/api/oslo/",)


def manage_grades_url(base_url: str, course: str) -> str:
    """URL of the Manage Grades list for a course org unit ID."""
    return f"{base_url}{MANAGE_PATH}gradeslist.d2l?ou={course}"


def edit_tab_urls(base_url: str, course: str, kind: str, object_id: int) -> dict[str, str]:
    """URLs of each edit tab for a grade object, keyed by tab name."""
    return {
        tab: f"{base_url}{MANAGE_PATH}{page}?objectId={object_id}&ou={course}"
        for tab, page in EDIT_TABS.get(kind, {}).items()
    }


def is_read_only_request(method: str, url: str) -> bool:
    """Whether the crawl may send this request.

    Blocks anything under Enter Grades and any non-GET request other than
    localization lookups, so the crawl cannot change the gradebook.
    """
    path = urlparse(url).path
    if "/grades/admin/enter/" in path:
        return False
    if method.upper() in ("GET", "HEAD"):
        return True
    return path.startswith(_ALLOWED_POST_PATHS)


def is_restrictions_write(method: str, url: str) -> bool:
    """Whether a request is a crawl-safe read or a save of a Restrictions tab.

    Used when editing start dates: the only write allowed is posting a grade
    item's Restrictions form. Enter Grades stays blocked.
    """
    if is_read_only_request(method, url):
        return True
    path = urlparse(url).path
    return "/grades/admin/enter/" not in path and path.endswith(
        f"/{MANAGE_PATH}{EDIT_TABS['item']['restrictions']}"
    )


def following_weekday(day: date, weekday: int = 4, at: time = time(12, 0)) -> datetime:
    """The first ``weekday`` (Monday=0 ... Friday=4) strictly after ``day``, at ``at``."""
    days_ahead = (weekday - day.weekday()) % 7 or 7
    return datetime.combine(day + timedelta(days=days_ahead), at)


# Tick "Has Start Date" first (a real click, so D2L's enabler runs), then set
# the picker; its change event syncs the hidden year/month/day/... inputs that
# the form submits. Returns those hidden values.
SET_START_DATE_JS = r"""(value) => {
  const box = document.querySelector(".js_startDateEdit");
  const picker = box && box.querySelector("d2l-input-date-time");
  if (!picker) throw new Error("Start date picker not found");
  picker.value = value;
  picker.dispatchEvent(new CustomEvent("change", { bubbles: true, composed: true }));
  const part = (p) => (document.getElementById(`${picker.id}$${p}`) || {}).value;
  return Object.fromEntries(["year", "month", "day", "hour", "minute", "isEnabled"].map((p) => [p, part(p)]));
}"""


# Rows of the Manage Grades table: id, kind, name, parent category, columns.
EXTRACT_GRADE_LIST_JS = r"""() => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const kinds = {
    gotoNewEditItemProps: "item",
    gotoNewEditCatProps: "category",
    gotoNewEditFinalGradeProps: "final_grade",
  };
  const first = document.querySelector("input[name='GradesList_cb']");
  if (!first) return { columns: [], rows: [] };
  const table = first.closest("table");
  const headerRow = [...table.querySelectorAll("tr")].find((tr) => tr.querySelector("th[scope='col']"));
  const columns = headerRow
    ? [...headerRow.children].map((c) => clean(c.innerText || c.textContent))
    : [];

  const rows = [];
  let category = null;
  for (const tr of table.querySelectorAll("tr")) {
    const cb = tr.querySelector("input[name='GradesList_cb']");
    const nameCell = tr.querySelector("th[scope='row']");
    const link = nameCell && nameCell.querySelector("a[onclick]");
    if (!cb || !link) continue;
    const m = (link.getAttribute("onclick") || "").match(/(gotoNewEdit\w+)\(\s*(\d+)\s*\)/);
    const kind = m ? kinds[m[1]] || m[1] : null;
    const id = m ? Number(m[2]) : null;
    // Items in a category are indented beneath the category's row.
    const indented = /padding-left/.test(nameCell.getAttribute("style") || "");
    if (kind === "category") category = { id, name: clean(link.textContent) };
    else if (!indented) category = kind === "item" ? null : category;

    const cells = {};
    [...tr.children].forEach((c, i) => {
      if (i === 0 || c === nameCell) return;
      cells[columns[i] || `column_${i}`] = clean(c.innerText || c.textContent);
    });
    rows.push({
      id,
      kind,
      name: clean(link.textContent),
      category: kind === "item" && indented ? category : null,
      columns: cells,
      indicators: [...nameCell.querySelectorAll("[title]")]
        .map((e) => clean(e.getAttribute("title")))
        .filter((t) => t && !t.startsWith("Edit ")),
    });
  }
  return { columns, rows };
}"""

# Fields, selector tables and text of the selected edit-page tab panel.
EXTRACT_TAB_PANEL_JS = r"""() => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const panel =
    document.querySelector("d2l-tab-panel[_selected]") ||
    document.querySelector("d2l-tab-panel[selected]") ||
    document.querySelector("form") ||
    document.body;

  const headings = [...panel.querySelectorAll("h2")];
  const sectionOf = (el) => {
    let section = null;
    for (const h of headings) {
      if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) section = clean(h.innerText || h.textContent);
    }
    return section;
  };

  // Legacy D2L forms put each field's label in its own row, followed by a
  // row (td.fct_w) holding the controls.
  const fieldOf = (el) => {
    let cell = el.closest("td.fct_w");
    while (cell) {
      const labelRow = cell.parentElement && cell.parentElement.previousElementSibling;
      const label = labelRow && labelRow.querySelector("td.fl_n .d2l-label");
      const text = label && clean(label.textContent);
      if (text) return text;
      cell = cell.parentElement && cell.parentElement.closest("td.fct_w");
    }
    return null;
  };

  const ownLabel = (el) => {
    if (el.id) {
      const l = panel.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && clean(l.textContent)) return clean(l.textContent);
    }
    return clean(el.getAttribute("aria-label") || el.getAttribute("label") || el.getAttribute("title")) || null;
  };

  const hidden = (id) => {
    const e = document.getElementById(id);
    return e ? e.value : null;
  };

  const fields = [];
  const controls = panel.querySelectorAll(
    "input, select, textarea, d2l-input-number, d2l-input-date-time, d2l-htmleditor"
  );
  for (const el of controls) {
    const tag = el.tagName.toLowerCase();
    if (tag === "input" && ["hidden", "button", "submit", "image"].includes(el.type)) continue;
    if (el.closest("d2l-htmleditor") && tag !== "d2l-htmleditor") continue;

    const rec = {
      section: sectionOf(el),
      field: fieldOf(el),
      label: ownLabel(el),
      name: el.getAttribute("name") || el.id || null,
      control: tag === "input" ? el.type : tag,
      disabled: el.hasAttribute("disabled"),
    };
    if (tag === "input" && (el.type === "checkbox" || el.type === "radio")) {
      rec.checked = el.checked;
      rec.value = el.value;
    } else if (tag === "select") {
      const opt = el.options[el.selectedIndex];
      rec.value = opt ? opt.value : null;
      rec.text = opt ? clean(opt.textContent) : null;
      rec.options = [...el.options].map((o) => ({ value: o.value, text: clean(o.textContent) }));
    } else if (tag === "d2l-input-number") {
      // The widget's displayed value can lag; the hidden input is what's submitted.
      const hiddenId = el.getAttribute("data-hidden-id");
      rec.value = hiddenId ? hidden(hiddenId) : el.getAttribute("value");
      rec.displayed = el.getAttribute("value");
    } else if (tag === "d2l-input-date-time") {
      const part = (p) => hidden(`${el.id}$${p}`);
      // When a date is disabled the widget still shows a placeholder date.
      rec.enabled = part("isEnabled") === null ? null : part("isEnabled") === "1";
      // Name the picker after its "Has Start Date"-style toggle, not just "Date".
      for (let a = el.parentElement; a && a !== panel; a = a.parentElement) {
        const toggle = a.querySelector(".d2l-checkbox-container label");
        if (toggle) { rec.label = clean(toggle.textContent).replace(/^Has /, ""); break; }
      }
      rec.value = el.getAttribute("value");
    } else if (tag === "d2l-htmleditor") {
      const h = el.querySelector("input.d2l-htmleditor-html");
      rec.value = h ? h.value : el.getAttribute("html");
      rec.label = rec.label || el.id;
    } else {
      rec.value = el.value;
    }
    fields.push(rec);
  }

  // Field rows that show a value without any control (e.g. "Type: Numeric").
  for (const label of panel.querySelectorAll("td.fl_n .d2l-label")) {
    const row = label.closest("tr");
    const valueCell = row && row.nextElementSibling && row.nextElementSibling.querySelector("td.fct_w");
    if (!valueCell || valueCell.querySelector("input:not([type=hidden]), select, textarea, d2l-input-number, d2l-input-date-time, d2l-htmleditor, table.dcs")) continue;
    const text = clean(valueCell.innerText || valueCell.textContent);
    if (clean(label.textContent)) {
      fields.push({ section: sectionOf(label), field: clean(label.textContent), control: "text", value: text });
    }
  }

  // Selector tables (rubrics, release conditions, ...): one entry per row.
  const lists = [];
  for (const table of panel.querySelectorAll("table.dcs")) {
    const rows = [...table.querySelectorAll("tr")]
      .filter((tr) => !tr.querySelector("button, .d2l-selector-emptytext"))
      .map((tr) => [...tr.querySelectorAll("td, th")].map((c) => clean(c.innerText || c.textContent)).filter(Boolean))
      .filter((cells) => cells.length);
    const empty = table.querySelector(".d2l-selector-emptytext");
    lists.push({
      section: sectionOf(table),
      field: fieldOf(table),
      rows,
      empty_text: empty ? clean(empty.textContent) : null,
    });
  }

  return { fields, lists, text: clean(panel.innerText || panel.textContent) };
}"""
