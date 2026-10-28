"""Tests for parsing and exporting WebAssign assignment and question source.

All data here is synthetic. The markup follows the structure of WebAssign's
My Classes page, Assignment Editor and Question Editor as served (before any
script runs).
"""

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from typer.testing import CliRunner

from edubag.webassign import app
from edubag.webassign import client as wa
from edubag.webassign.client import WebAssignClient
from edubag.webassign.questions import (
    image_paths,
    mode_names,
    parse_assignment_editor,
    parse_assignment_list,
    parse_question_editor,
    redact,
    user_pass,
)

SECRET = "0123456789abcdef0123456789abcdef"

QUESTION_SOURCE = """<link href="/book/feedback.css" rel="stylesheet" type="text/css" />
<eqn>
$a = randnum(2, 6, 1);
$image = "/book/figure-1.gif";
$other = "/book/$name.gif";
''</eqn>
<div class = indent>
    <watex>\\[\\sum^{<s:infinity>}_{n = 1}{\\arctan ${a}n}\\]</watex>
</div>
<_><br />
If it is convergent, find its sum. 0 &lt; x</textarea-ish>
<SECTION NOBR> <_>"""
ANSWER_SOURCE = "<EQN $ORDERED=2; ''>convergent\ndivergent\n<SECTION><EQN $PAD='calc';''>DIVERGES"
SOLUTION_SOURCE = "<latex eqn='black'>\\noindent $ \\sum a_n $ diverges.</latex>"


def _escape(text: str) -> str:
    # WebAssign escapes only some entities inside the textareas, and sends CRLF line breaks.
    return text.replace("&", "&amp;").replace("\n", "\r\n")


QUESTION_EDITOR = f"""<html><head><title>Question Editor</title></head><body>
<form name='wa' id='wa' method='post' action='https://www.webassign.net/v4cgi/faculty.pl'>
<input type='hidden' name='CSRFToken' value='token'>
<input type='hidden' name='UserPass' id='UserPass' value='{SECRET}'>
<input type='hidden' name='action' id='action' value='questions/edit'>
<div class="qe-editor-div-col-60">
  <h1 style="display:inline"><font size="+1"><b>Question Editor</b> --
  Editing <b>Book 8.2.021.</b> (2000001)</font></h1>
</div>
<div class="qe-editor-div-col-40">Last Saved:
  <a class="qe-target-link" href="javascript:openWindow('util/history.tpl?id=2000001&amp;UserPass={SECRET}')">
  May 24, 2012 02:20 PM EDT</a></div>
<label for="code">Name</label>
<input type='text' name='code' id='code' value='Book 8.2.021.' size='30' maxlength='50' />
<select name='mode' id='mode'>
  <option value='C'>Multiple-Choice</option>
  <option value='Q'>Symbolic</option>
  <option value='CQ' selected>Multi-Mode...CQ</option>
</select>
<input type='hidden' name='defaultMode' id='defaultMode' value='CQ'>
<select name='qsize' id='qsize'>
  <option value='Small'>Small</option><option value='Medium' selected>Medium</option></select>
<textarea name='question' id='question' maxlength='131072' wrap='soft'>{_escape(QUESTION_SOURCE)}</textarea>
<textarea name='answer' id='answer' wrap='soft' rows='13' cols='75'>{_escape(ANSWER_SOURCE)}</textarea>
<textarea name='solution' id='solution' wrap='soft' rows='13' cols='75'>
{_escape(SOLUTION_SOURCE)}</textarea>
<div class="display">
  <input type="hidden" name="RC_0_0_0_70" id="RC_0_0_0_70" value="mc">
  <input type="radio" name="RC_0_0_0_70" id="RC_0_0_0_70_0" value="0">
  <input type="hidden" name="enabled_RQ_0_0_1_70" value="1">
</div>
<div class="qe-editor-layout qe-additional-info">
  <div class="qe-editor-div-row">
    <div class="qe-editor-div-cell qe-editor-div-col-20"><font>Textbook</font></div>
    <div class="qe-editor-div-cell qe-editor-div-col-80">
<input type="hidden" name="textbook" id="textbook" value="1005"><b>Author, "Calculus", ed. 2</b></div>
  </div>
  <div class="qe-editor-div-row">
    <div class="qe-editor-div-cell qe-editor-div-col-20"><font>Author</font></div>
    <div class="qe-editor-div-cell qe-editor-div-col-80">
<input type="hidden" name="author" id="author" value="webassign"><b>WebAssign Staff</b> (support@example.com)</div>
  </div>
  <div class="qe-editor-div-row">
    <div class="qe-editor-div-cell qe-editor-div-col-20"><font>Locked</font></div>
    <div class="qe-editor-div-cell qe-editor-div-col-80">
<input type="hidden" name="locked" id="locked" value="Y"><b>Yes</b></div>
  </div>
</div>
</form>
<form name='quickSearch'><input type='text' name='mybox' value=''></form>
</body></html>"""

CONTENTS = [
    {
        "type": "question",
        "id": "2000001",
        "code": "Book 8.2.021.",
        "mode": "CQ",
        "chapter": "8",
        "textbook": {"textbook_name": "Calculus", "textbook_id": "1005"},
        "view_link": [f"questions/preview.tpl?qid=2000001&UserPass={SECRET}", "V2000001", ""],
        "items": [
            {"name": "a", "mode": "C", "points": "1"},
            {"name": "b", "mode": "Q", "points": "2"},
        ],
    },
    {
        "type": "pool",
        "id": "77",
        "questions": [
            {"type": "question", "id": "2000002", "code": "Book 8.2.022.", "mode": "A",
             "items": [{"name": "a", "mode": "A", "points": "1"}]},
        ],
    },
]
SETTINGS = {"submissions_allowed": 5, "randomization": "P", "name": "Homework"}

ASSIGNMENT_EDITOR = f"""<html><head><title>Assignment Editor</title></head><body>
<form name='wa' id='wa' method='post'>
<input type='hidden' name='UserPass' value='{SECRET}'>
<input type='hidden' name='aid' id='aid' value='3000001'>
<h1><b>Assignment Editor</b> -- Editing <b>Chap 8.2</b> (3000001)</h1>
Last Saved: Aug 12, 2026 04:58 PM EDT <a>View History</a>
<input type='text' name='assignmentName' id='assignmentName' value='Chap 8.2'>
<textarea name='heading' id='heading'>Show <b>all</b> work.</textarea>
<input type='text' name='keywords' id='keywords' value='series'>
<input type='hidden' name='author' id='author' value='prof@nyu'>
</form>
<script>
window.addEvent('domready', function() {{
    Assignment.Editor.getInstance().initializeSettings( unescape( "{quote(json.dumps(SETTINGS))}" ) );
}});
    Assignment.Editor.getInstance().contents = new Assignment.Editor.Content(
        unescape("{quote(json.dumps(CONTENTS))}") );
</script>
</body></html>"""


def _assignment_row(aid: str, name: str, deployment: str, section: str, due: str) -> str:
    edit = (
        "javascript:onClick=document.forms[0].action.value='assignments/edit'; "
        f"document.forms[0].aid.value='{aid}'; document.forms[0].submit(this)"
    )
    return f"""<tr>
 <td width="75%"><div>
   <div><a href="{edit}"><font size="-1"><b>{name}</b></font></a></div>
   <div>
    <a href="javascript:openWindow('/web/Faculty/Assignment-Preview/deployment?id={deployment}')">view</a> |
    <a href="{edit}">edit</a> |
    <a href="javascript:onClick=document.forms[0].action.value='grades/scores';
        document.forms[0].deployment.value='{deployment}';
        document.forms[0].clicked.value='1299802,{section}';
        document.forms[0].submit(this)">scores</a>
   </div></div></td>
 <td width="25%" align="right"><font size="-1">{due}</font></td>
</tr>"""


MY_CLASSES = f"""<html><body><form name='wa' id='wa'>
<table class="assignmentsTable" aria-label="Current/Recent Assignments">
<thead><tr><th>Name</th><th>Due</th></tr></thead><tbody>
{_assignment_row("3000001", "Chap 8.2", "4000001", "1691600", "10-23-26&nbsp;08:00 AM&nbsp;EDT")}
{_assignment_row("3000002", "Chap 8.3", "4000002", "1691600", "10-30-26&nbsp;08:00 AM&nbsp;EDT")}
</tbody></table>
<table class="assignmentsTable" aria-label="Current/Recent Assignments">
<thead><tr><th>Name</th><th>Due</th></tr></thead><tbody>
{_assignment_row("3000001", "Chap 8.2", "4000101", "1691636", "10-24-26&nbsp;08:00 AM&nbsp;EDT")}
</tbody></table>
</form></body></html>"""


def test_parse_question_editor_keeps_source_verbatim():
    q = parse_question_editor(QUESTION_EDITOR)
    assert q.qid == "2000001"
    assert q.name == "Book 8.2.021."
    assert q.question == QUESTION_SOURCE
    assert q.answer == ANSWER_SOURCE
    assert q.solution == SOLUTION_SOURCE


def test_parse_question_editor_metadata():
    q = parse_question_editor(QUESTION_EDITOR)
    assert q.mode == "CQ"
    assert q.mode_names == ["Multiple-Choice", "Symbolic"]
    assert q.last_saved == "May 24, 2012 02:20 PM EDT"
    assert q.info == {
        "Textbook": 'Author, "Calculus", ed. 2',
        "Author": "WebAssign Staff (support@example.com)",
        "Locked": "Yes",
    }
    assert q.fields == {
        "defaultMode": "CQ",
        "qsize": "Medium",
        "textbook": "1005",
        "author": "webassign",
        "locked": "Y",
    }
    assert q.images == [{"src": "/book/figure-1.gif"}]


def test_parse_question_editor_drops_session_secrets():
    assert SECRET not in json.dumps(parse_question_editor(QUESTION_EDITOR).to_dict())


def test_parse_question_editor_rejects_other_pages():
    with pytest.raises(ValueError, match="No question source"):
        parse_question_editor(ASSIGNMENT_EDITOR)


def test_parse_assignment_editor():
    a = parse_assignment_editor(ASSIGNMENT_EDITOR)
    assert (a.aid, a.name, a.keywords, a.author) == ("3000001", "Chap 8.2", "series", "prof@nyu")
    assert a.instructions == "Show <b>all</b> work."
    assert a.last_saved == "Aug 12, 2026 04:58 PM EDT"
    assert a.settings == SETTINGS
    assert [(q["position"], q["qid"], q["pool"]) for q in a.questions] == [(1, "2000001", None), (2, "2000002", "77")]
    first = a.questions[0]
    assert first["code"] == "Book 8.2.021."
    assert first["mode_names"] == ["Multiple-Choice", "Symbolic"]
    assert first["parts"] == [{"name": "a", "mode": "C", "points": "1"}, {"name": "b", "mode": "Q", "points": "2"}]
    assert first["points"] == 3.0
    assert first["textbook"] == "Calculus"
    assert len(a.contents) == 2
    assert SECRET not in json.dumps(a.to_dict())


def test_parse_assignment_editor_rejects_other_pages():
    with pytest.raises(ValueError, match="No assignment content"):
        parse_assignment_editor(QUESTION_EDITOR)


def test_parse_assignment_list():
    rows = parse_assignment_list(MY_CLASSES)
    assert rows == [
        {"aid": "3000001", "name": "Chap 8.2", "deployment_id": "4000001", "course_id": "1299802",
         "section_id": "1691600", "due": "10-23-26 08:00 AM EDT"},
        {"aid": "3000002", "name": "Chap 8.3", "deployment_id": "4000002", "course_id": "1299802",
         "section_id": "1691600", "due": "10-30-26 08:00 AM EDT"},
        {"aid": "3000001", "name": "Chap 8.2", "deployment_id": "4000101", "course_id": "1299802",
         "section_id": "1691636", "due": "10-24-26 08:00 AM EDT"},
    ]


def test_to_json_round_trip(tmp_path: Path):
    q = parse_question_editor(QUESTION_EDITOR)
    path = q.to_json(tmp_path / "questions" / "2000001.json")
    assert json.loads(path.read_text()) == q.to_dict()


def test_helpers():
    assert mode_names("N") == ["Numerical"]
    assert mode_names("CQ") == ["Multiple-Choice", "Symbolic"]
    assert image_paths('<img src="/a/b.PNG">', "$x = '/c/$d.gif'; $y = \"/e/f.jpg\";") == ["/a/b.PNG", "/e/f.jpg"]
    assert user_pass(f"https://www.webassign.net/v4cgi/faculty.pl?action=&UserPass={SECRET}") == SECRET
    assert user_pass("https://www.webassign.net/v4cgi/faculty.pl") is None
    assert redact(f"UserPass={SECRET}", SECRET) == "UserPass=REDACTED"
    assert redact("text", None) == "text"


def test_client_helpers():
    assert wa.assignment_id(" 3000001 ") == "3000001"
    with pytest.raises(ValueError, match="Invalid WebAssign assignment ID"):
        wa.assignment_id("Chap 8.2")
    assert wa.assignment_date_value("All") == "all"
    with pytest.raises(ValueError, match="Unknown assignment list"):
        wa.assignment_date_value("recent")
    assert wa.export_dir_name("3000001", "Chap 8.2: Series!") == "3000001-Chap-8-2-Series"
    assert wa.export_dir_name("3000001", "") == "3000001"


def test_invalid_options_rejected_before_browser(tmp_path: Path, monkeypatch):
    client = WebAssignClient(auth_state_path=tmp_path / "auth.json")
    monkeypatch.setattr(wa, "sync_playwright", lambda: pytest.fail("browser should not launch"))
    with pytest.raises(ValueError, match="Invalid WebAssign assignment ID"):
        client.export_assignment("Chap 8.2")
    with pytest.raises(ValueError, match="Unknown assignment list"):
        client.list_assignments("1691600", when="recent")


class FakeResponse:
    def __init__(self, url: str, body: bytes = b"", status: int = 200):
        self.url, self._body, self.status = url, body, status
        self.ok = status < 400

    def text(self) -> str:
        return self._body.decode()

    def body(self) -> bytes:
        return self._body


class FakeContext:
    """A browser context whose requests are answered from a dict of URL -> response."""

    def __init__(self, responses: dict[str, FakeResponse]):
        self.requested: list[str] = []
        responses = responses

        class Request:
            @staticmethod
            def get(url: str) -> FakeResponse:
                self.requested.append(url)
                return responses.get(url, FakeResponse(url, status=404))

        self.request = Request()


def test_export_question_downloads_images_and_redacts(tmp_path: Path):
    client = WebAssignClient(auth_state_path=tmp_path / "auth.json")
    editor_url = f"https://www.webassign.net/v4cgi/faculty.pl?action=questions/edit&qid=2000001&UserPass={SECRET}"
    image_url = "https://www.webassign.net/book/figure-1.gif"
    context = FakeContext({
        editor_url: FakeResponse(editor_url, QUESTION_EDITOR.encode()),
        image_url: FakeResponse(image_url, b"GIF89a"),
    })
    q = client._export_question(context, "2000001", tmp_path, SECRET, keep_html=True)
    assert context.requested == [editor_url, image_url]
    assert q.source_url == "https://www.webassign.net/v4cgi/faculty.pl?action=questions/edit&qid=2000001"
    assert q.images == [{"src": "/book/figure-1.gif", "file": "questions/2000001/figure-1.gif"}]
    assert (tmp_path / "questions" / "2000001" / "figure-1.gif").read_bytes() == b"GIF89a"
    kept = (tmp_path / "html" / "question-2000001.html").read_text()
    assert SECRET not in kept
    assert "REDACTED" in kept


def test_fetch_page_detects_expired_session(tmp_path: Path):
    client = WebAssignClient(auth_state_path=tmp_path / "auth.json")
    url = "https://www.webassign.net/v4cgi/faculty.pl?action=assignments/edit&aid=3000001"
    login = "https://www.webassign.net/wa-auth/login"
    context = FakeContext({url: FakeResponse(login, b"<html></html>")})
    with pytest.raises(wa.WebAssignAuthError):
        client._fetch_page(context, "/v4cgi/faculty.pl?action=assignments/edit&aid=3000001")


@pytest.mark.parametrize("command", ["list-assignments", "export-assignment"])
def test_cli_help(command: str):
    result = CliRunner().invoke(app, ["client", command, "--help"])
    assert result.exit_code == 0


def test_cli_export_assignment(tmp_path: Path, monkeypatch):
    calls = []

    def fake_export(self, assignment, **kwargs):
        calls.append((assignment, kwargs))
        return tmp_path / assignment

    monkeypatch.setattr(WebAssignClient, "export_assignment", fake_export)
    result = CliRunner().invoke(
        app, ["client", "export-assignment", "3000001", "3000002", "--save-dir", str(tmp_path), "--keep-html"]
    )
    assert result.exit_code == 0, result.output
    assert [a for a, _ in calls] == ["3000001", "3000002"]
    assert calls[0][1]["keep_html"] is True
    assert calls[0][1]["save_dir"] == tmp_path
    assert "exported to" in result.output
