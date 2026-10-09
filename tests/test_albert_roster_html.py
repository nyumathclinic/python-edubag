"""Tests for saving, parsing and exporting Albert HTML class rosters.

All data here is synthetic; no real roster or photo is used. The markup
follows the structure of Albert's Fluid class roster page.
"""

import json
import sqlite3
import zipfile

import pytest
import vobject

from edubag.albert.client import AlbertClient
from edubag.albert.exports import course_label, write_anki_deck, write_vcards
from edubag.albert.roster import AlbertRoster, large_photo_src, unpack_progplan

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"synthetic photo" + b"\xff\xd9"
FAKE_LARGE_JPEG = b"\xff\xd8\xff\xe0" + b"synthetic large photo" + b"\xff\xd9"

STUDENTS = [
    # (row, name, campus id, netid, program and plan, level, photo src or None)
    (0, "Annie Freeman", "N10000001", "af1001",
     "UA-Coll of Arts &amp; Sci - <br>\nMathematics", "Sophomore", "files/0.jpg"),
    (1, "Antonio Luis Hernandez", "N10000002", "ah1002",
     "UF-Global Liberal Studies - <br>\nCore", "Freshman", "files/1.jpg"),
    (2, "Bonnie Lawson", "N10000003", "bl1003", "UA-Coll of Arts &amp; Sci - <br>\nUndecided", "Junior", None),
]


def _field(kind, field, label, value, row, inner=""):
    """One labeled field box, as Albert renders it (inner is e.g. "$383$")."""
    suffix = f"{inner}${row}" if inner else f"${row}"
    return (
        f'<div class="ps_box-{kind} psc_disabled psc_has_value" id="win0div{field}{suffix}">'
        f'<div class="ps_box-label" id="win0div{field}lbl${row}"><span class="ps-label">{label}</span></div>'
        f'<span class="ps_box-value" id="{field}{suffix}">{value}</span></div>'
    )


def student_box(row, name, campus_id, netid, progplan, level, photo):
    img = (
        f'<div class="ps_box-img" id="win0divNYU_EMP_SPIC_VW_EMPLOYEE_PHOTO$382$${row}">'
        f'<img src="{photo}" class="ps-img" alt="{name}\'s Photo" id="NYU_EMP_SPIC_VW_EMPLOYEE_PHOTO$382$${row}"></div>'
        if photo
        else ""
    )
    return f"""
    <div class="ps_box-group psc_layout psc_column-2 nyu_group_outline" id="win0divBIGGRP${row}">
      <div class="ps_box-group psc_layout psc_columnitem-1of2" id="win0div$ICField396${row}">
        <input type="hidden" class="psc_off" id="DERIVED_AA2_SELECT$chk${row}" value="N">
        {img}
        {_field("edit", "SCC_PRFPRIMNMVW_NAME_DISPLAY", "Name", name, row, "$383$")}
        <img title="No Name Recording" src="/cs/csprod/cache/861/NYU_PRONOUN_NOREC_1.PNG/">
        {_field("edit", "NYU_NMCOACH_WRK_NYU_PRONOUN", "Pronoun", "&nbsp;", row)}
        {_field("edit", "NYU_GRD_RST_VW_CAMPUS_ID", "Campus ID", campus_id, row)}
        {_field("edit", "SCC_PREF_EMAIL_EMAIL_ADDR", "Email", f"{netid}@nyu.edu", row)}
        {_field("edit", "SCC_PREF_PHN_VW_PHONE", "Telephone", "555/555-0100", row, "$390$")}
        {_field("dropdown", "DROP_REASON1", "Drop Reason", "&nbsp; ", row)}
      </div>
      <div class="ps_box-group psc_layout psc_columnitem-1of2" id="win0div$ICField397${row}">
        {_field("edit", "ACAD_CAR_TBL_DESCR", "Academic Career", "Undergraduate", row, "$402$")}
        {_field("longedit", "PROGPLAN2", "Program and Plan", progplan, row)}
        {_field("dropdown", "CLASS_ROSTER_VW_ACADEMIC_LEVEL", "Academic Level", level, row)}
        {_field("edit", "NYU_NMCOACH_WRK_DESCR50", "Study Away Location", "&nbsp;", row)}
      </div>
    </div>"""


HEADER = """
<div class="ps_box-group psc_layout" id="win0divROSTER_HDRGRP">
  <div class="ps_box-edit psc_disabled psc_has_value" id="win0divDERIVED_SSR_FC_DESCR254">
    <div class="ps_box-label" id="win0divDERIVED_SSR_FC_DESCR254lbl"><span class="ps-label">&nbsp;</span></div>
    <span class="ps_box-value" id="DERIVED_SSR_FC_DESCR254">Calculus II (10488) (Lecture)</span></div>
  <div class="ps_box-edit psc_disabled psc_has_value" id="win0divDERIVED_SSR_FC_CLASS_SECTION">
    <div class="ps_box-label" id="win0divDERIVED_SSR_FC_CLASS_SECTIONlbl"><span class="ps-label">Section</span></div>
    <span class="ps_box-value" id="DERIVED_SSR_FC_CLASS_SECTION">016</span></div>
  <div class="ps_box-edit psc_disabled psc_icon psc_has_value" id="win0divTERM_VAL_TBL_DESCR">
    <div class="ps_box-label" id="win0divTERM_VAL_TBL_DESCRlbl"><span class="ps-label">Term</span></div>
    <span class="ps_box-value" id="TERM_VAL_TBL_DESCR">Fall 2026</span></div>
</div>"""


def roster_page_html(photo_srcs=None):
    """Synthetic roster page; photo_srcs overrides each student's img src."""
    boxes = []
    for i, (row, name, campus_id, netid, progplan, level, photo) in enumerate(STUDENTS):
        if photo and photo_srcs:
            photo = photo_srcs[i]
        boxes.append(student_box(row, name, campus_id, netid, progplan, level, photo))
    return f"<html><body>{HEADER}{''.join(boxes)}</body></html>"


@pytest.fixture
def roster_html(tmp_path):
    (tmp_path / "files").mkdir()
    for *_, photo in STUDENTS:
        if photo:
            (tmp_path / photo).write_bytes(FAKE_JPEG)
    path = tmp_path / "Roster.html"
    path.write_text(roster_page_html(), encoding="utf-8")
    return path


def test_unpack_progplan():
    assert unpack_progplan("UA-Coll of Arts & Sci - \n\nUndecided") == ("UA-Coll of Arts & Sci", "Undecided")
    assert unpack_progplan("Non-Degree") == ("Non-Degree", "")


def test_from_html_course(roster_html):
    roster = AlbertRoster.from_html(roster_html)
    assert roster.course["course_name"] == "Calculus II"
    assert roster.course["class_type"] == "Lecture"
    assert roster.course["Class Number"] == "10488"
    assert roster.course["Section"] == "016"
    assert roster.course["Semester"] == "Fall 2026"
    assert roster.pathstem == "class10488_016_1268"
    assert course_label(roster) == "Calculus II - 016, Fall 2026"


def test_from_html_students(roster_html):
    students = AlbertRoster.from_html(roster_html).students
    assert list(students["netid"]) == ["af1001", "ah1002", "bl1003"]
    second = students.iloc[1]
    assert (second["first"], second["last"]) == ("Antonio Luis", "Hernandez")
    assert (second["program"], second["plan"]) == ("UF-Global Liberal Studies", "Core")
    assert second["campus_id"] == "N10000002"
    assert second["level"] == "Freshman"
    assert second["career"] == "Undergraduate"
    assert second["phone"] == "555/555-0100"
    assert second["photo"].endswith("files/1.jpg")
    # Blank (&nbsp;) fields are missing, not empty strings.
    assert students["pronoun"].isna().all() and students["drop_reason"].isna().all()
    assert students["photo"].isna().tolist() == [False, False, True]


def test_json_round_trip(roster_html, tmp_path):
    out = tmp_path / "out" / "section.json"
    AlbertRoster.from_html(roster_html).to_json(out)
    data = json.loads(out.read_text())
    assert data["students"][0]["photo"] == "section_photos/af1001.jpg"
    assert data["students"][2]["photo"] is None
    assert (out.parent / "section_photos" / "af1001.jpg").read_bytes() == FAKE_JPEG

    loaded = AlbertRoster.from_json(out)
    assert loaded.course["Section"] == "016"
    assert loaded.photo_path(loaded.students.iloc[0]["photo"]).read_bytes() == FAKE_JPEG


def test_write_vcards(roster_html, tmp_path):
    roster = AlbertRoster.from_json(AlbertRoster.from_html(roster_html).to_json(tmp_path / "s.json"))
    path = write_vcards(roster, tmp_path / "s.vcf")
    cards = list(vobject.readComponents(path.read_text()))
    assert [c.fn.value for c in cards] == ["Annie Freeman", "Antonio Luis Hernandez", "Bonnie Lawson"]
    assert cards[1].n.value.family == "Hernandez"
    assert cards[0].email.value == "af1001@nyu.edu"
    assert cards[0].contents["x-nyu-nnumber"][0].value == "N10000001"
    assert cards[0].photo.value == FAKE_JPEG
    assert not hasattr(cards[2], "photo")
    assert cards[0].contents["x-abrelatednames"][0].value == "Calculus II - 016, Fall 2026"


def test_write_anki_deck(roster_html, tmp_path):
    roster = AlbertRoster.from_json(AlbertRoster.from_html(roster_html).to_json(tmp_path / "s.json"))
    first = write_anki_deck(roster, tmp_path / "deck1.apkg")
    second = write_anki_deck(roster, tmp_path / "deck2.apkg")

    def notes(apkg):
        with zipfile.ZipFile(apkg) as z:
            media = json.loads(z.read("media"))
            (tmp_path / "collection.anki2").write_bytes(z.read("collection.anki2"))
        with sqlite3.connect(tmp_path / "collection.anki2") as db:
            rows = db.execute("select guid, flds from notes order by sfld").fetchall()
        return sorted(media.values()), rows

    media, rows = notes(first)
    assert media == ["edubag_af1001.jpg", "edubag_ah1002.jpg"]  # student without photo skipped
    assert len(rows) == 2 and "Annie Freeman" in rows[0][1]
    assert notes(second)[1] == rows  # stable guids, so re-import updates cards
    assert not (tmp_path / ".deck1_media").exists()


class _FakeResponse:
    def __init__(self, body, ok=True):
        self._body, self.ok, self.status = body, ok, 200 if ok else 404
        self.headers = {"content-type": "image/jpeg" if ok else "text/html; charset=utf-8"}

    def body(self):
        return self._body


class _FakeRequest:
    def __init__(self, missing=()):
        self.urls = []
        self.missing = set(missing)

    def get(self, url):
        self.urls.append(url)
        if url in self.missing:
            return _FakeResponse(b"<html>Not Found</html>", ok=False)
        return _FakeResponse(FAKE_LARGE_JPEG if "/EMPL_PHOTO_" in url else FAKE_JPEG)


class _FakeContext:
    def __init__(self):
        self.request = _FakeRequest()


class _FakePage:
    """Just enough of a Playwright page for _save_html_roster_page."""

    url = "https://sis.nyu.edu/psc/csprod/EMPLOYEE/SA/c/NYU_SR_FL.NYU_CLASSROSTER_FL.GBL?Page=x"

    def __init__(self, html):
        self._html = html
        self.context = _FakeContext()

    def content(self):
        return self._html

    def locator(self, selector):
        # No live header, so names come from the saved HTML.
        return _FakeLocator()


class _FakeLocator:
    def count(self):
        return 0


def test_large_photo_src():
    assert (
        large_photo_src("/cs/csprod/cache/861/NYU_EMP_SPIC_VW_GE1TMNBUGUZDS=_2000000000.JPG")
        == "/cs/csprod/cache/861/EMPL_PHOTO_GE1TMNBUGUZDS=_2000000000.JPG"
    )
    assert large_photo_src("files/0.jpg") is None


def test_save_html_roster_page_downloads_photos(tmp_path):
    base = "https://sis.nyu.edu/cs/csprod/cache/861/"
    server_srcs = [
        "/cs/csprod/cache/861/NYU_EMP_SPIC_VW_AAAA=_2000000000.JPG",
        "/cs/csprod/cache/861/NYU_EMP_SPIC_VW_BBBB=_2000000000.JPG",
    ]
    page = _FakePage(roster_page_html(server_srcs))
    # The second student has no large photo (Albert's placeholder), so it 404s.
    page.context.request.missing = {f"{base}EMPL_PHOTO_BBBB=_2000000000.JPG"}
    path = AlbertClient()._save_html_roster_page(page, tmp_path)

    assert path == tmp_path / "class10488_016_1268.html"
    # Only student photos are fetched (not the name-recording icon), via the page's
    # session: the large photo first, the thumbnail only when that fails.
    assert page.context.request.urls == [
        f"{base}EMPL_PHOTO_AAAA=_2000000000.JPG",
        f"{base}EMPL_PHOTO_BBBB=_2000000000.JPG",
        f"{base}NYU_EMP_SPIC_VW_BBBB=_2000000000.JPG",
    ]
    files = tmp_path / "class10488_016_1268_files"
    assert (files / "0.jpg").read_bytes() == FAKE_LARGE_JPEG
    assert (files / "1.jpg").read_bytes() == FAKE_JPEG

    roster = AlbertRoster.from_html(path)
    assert roster.photo_path(roster.students.iloc[1]["photo"]).read_bytes() == FAKE_JPEG
    assert roster.students["photo"].isna().tolist() == [False, False, True]


def test_pathstem_without_term_does_not_raise():
    roster = AlbertRoster.from_course({"Class Number": "10488", "Section": "016"})
    assert roster.pathstem == "class10488_016_UNKNOWN"
