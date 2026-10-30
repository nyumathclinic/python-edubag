#!/usr/bin/env python
"""Tests for the gradescope exam setup module."""

import json
from unittest.mock import Mock, patch

import pytest

from edubag.gradescope import setup as gs_setup
from edubag.gradescope.setup import (
    assignments_titled,
    check_outline,
    container_of,
    create_version_set,
    existing_rubric,
    import_question_rubric,
    leaf_questions,
    load_exam_config,
    match_rubric_file,
    normalize_rubric,
    outline_from_page,
    outline_indexes,
    same_rubric,
    save_outline,
)

# A free-response question in the style of a quiz rubric: one ungrouped item
# after two select-one groups.
FRQ = {
    "question": "4",
    "scoring_type": "positive",
    "floor": True,
    "ceiling": True,
    "rubric_items": [{"description": "No response", "weight": 0, "position": 2}],
    "rubric_item_groups": [
        {
            "description": "Why each side counts it",
            "mutually_exclusive": True,
            "position": 1,
            "rubric_items": [{"description": "Both sides", "weight": 2}, {"description": "One side", "weight": 1}],
        },
        {
            "description": "What is counted",
            "mutually_exclusive": True,
            "position": 0,
            "rubric_items": [{"description": "Clear", "weight": 2}, {"description": "Vague", "weight": 1}],
        },
    ],
}

CATA = {
    "question": "1",
    "scoring_type": "negative",
    "floor": True,
    "ceiling": True,
    "rubric_items": [
        {"description": "A **should** be selected", "weight": 0.4},
        {"description": "Correct", "weight": 0},
    ],
}


class TestLoadExamConfig:
    def test_versions_get_default_titles_and_resolved_files(self, tmp_path):
        (tmp_path / "rubric-0930.json").write_text("{}")
        (tmp_path / "q-0930.pdf").write_bytes(b"%PDF")
        config_path = tmp_path / "gradescope.yml"
        config_path.write_text(
            "course: 123\n"
            "title: Quiz 6\n"
            "versions:\n"
            "  '0930': {pdf: q-0930.pdf, rubric: rubric-0930.json}\n"
            "  '1100': {assignment: '98765'}\n"
        )

        config = load_exam_config(config_path)

        assert config.course == "123"
        assert [v.assignment for v in config.versions] == ["Quiz 6 - 0930", "98765"]
        assert config.versions[0].rubric == (tmp_path / "rubric-0930.json").resolve()
        assert config.versions[1].rubric is None

    def test_title_format(self, tmp_path):
        config_path = tmp_path / "gradescope.yml"
        config_path.write_text("course: 1\ntitle: Midterm II\ntitle_format: '{title} ({version})'\nversions: {A: {}}\n")

        assert load_exam_config(config_path).versions[0].assignment == "Midterm II (A)"

    def test_missing_keys_and_files_are_all_reported(self, tmp_path):
        config_path = tmp_path / "gradescope.yml"
        config_path.write_text("title: Quiz 6\nversions: {A: {rubric: nope.json}}\n")

        with pytest.raises(ValueError, match="missing 'course'"):
            load_exam_config(config_path)

        config_path.write_text("course: 1\ntitle: Quiz 6\nversions: {A: {rubric: nope.json}}\n")
        with pytest.raises(ValueError, match="rubric file not found"):
            load_exam_config(config_path)


class TestNormalizeRubric:
    def test_entries_follow_positions(self):
        rubric, errors = normalize_rubric(FRQ)

        assert errors == []
        assert rubric["settings"] == {"scoring_type": "positive", "floor": True, "ceiling": True}
        assert [e.get("item", e.get("group"))["description"] for e in rubric["entries"]] == [
            "What is counted",
            "Why each side counts it",
            "No response",
        ]
        assert rubric["entries"][0]["group"]["mutually_exclusive"] is True

    def test_without_positions_items_come_first(self):
        data = {
            "rubric_item_groups": [{"description": "G", "rubric_items": [{"description": "x", "weight": 1}]}],
            "rubric_items": [{"description": "Correct", "weight": 2}],
        }

        rubric, errors = normalize_rubric(data)

        assert errors == []
        assert ["item" in e for e in rubric["entries"]] == [True, False]

    def test_flat_form_puts_items_in_their_group(self):
        data = {
            "rubric_item_groups": [{"id": 7, "description": "G"}],
            "rubric_items": [{"description": "x", "weight": 1, "group_id": 7}],
        }

        rubric, errors = normalize_rubric(data)

        assert errors == []
        assert rubric["entries"] == [
            {"group": {"description": "G", "mutually_exclusive": False, "items": [{"description": "x", "weight": 1.0}]}}
        ]

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            ({"scoring_type": "both"}, "scoring_type"),
            ({"floor": "yes"}, "floor"),
            ({"rubric_items": [{"description": "x", "weight": "lots"}]}, "weight"),
            ({"rubric_items": [{"description": "", "weight": 1}]}, "description"),
            ({"rubric_item_groups": [{"description": "G", "rubric_items": []}]}, "at least one item"),
            ({"rubric_items": [{"description": "x", "weight": 1, "group_id": 3}]}, "no group with id 3"),
        ],
    )
    def test_problems_are_reported(self, data, message):
        _, errors = normalize_rubric(data)

        assert any(message in e for e in errors)


class TestMatchRubricFile:
    QUESTIONS = [
        {"id": 11, "full_index": "1", "title": "Q1"},
        {"id": 40, "full_index": "4", "title": "Q4"},
    ]

    def test_entries_match_by_question_number(self):
        entries, errors, warnings = match_rubric_file({"questions": [FRQ, CATA]}, self.QUESTIONS)

        assert errors == [] and warnings == []
        assert [q["id"] for q, _ in entries] == [40, 11]

    def test_unknown_and_repeated_questions(self):
        data = {"questions": [CATA, CATA, {"question": "9"}]}

        _, errors, _ = match_rubric_file(data, self.QUESTIONS)

        assert any("appears more than once" in e for e in errors)
        assert any("no question 9" in e for e in errors)

    def test_title_mismatch_is_a_warning(self):
        _, errors, warnings = match_rubric_file({"questions": [CATA | {"title": "Other"}]}, self.QUESTIONS)

        assert errors == []
        assert warnings == ['question 1 is titled "Q1" here, "Other" in the file']

    def test_leaf_questions_skip_parents(self):
        tree = [{"id": 1, "children": [{"id": 2}, {"id": 3, "children": []}]}, {"id": 4}]

        assert [q["id"] for q in leaf_questions(tree)] == [2, 3, 4]


class TestSameRubric:
    def test_matching_rubric_in_another_order(self):
        rubric, _ = normalize_rubric(FRQ)
        props = {
            "rubric_item_groups": [
                {"id": 1, "question_id": 40, "description": "What is counted"},
                {"id": 2, "question_id": 40, "description": "Why each side counts it"},
            ],
            "rubric_items": [
                {"id": 9, "question_id": 40, "description": "No response", "weight": "0.0"},
                {"id": 5, "question_id": 40, "group_id": 2, "description": "One side", "weight": "1.0"},
                {"id": 6, "question_id": 40, "group_id": 2, "description": "Both sides", "weight": "2.0"},
                {"id": 7, "question_id": 40, "group_id": 1, "description": "Vague", "weight": "1.0"},
                {"id": 8, "question_id": 40, "group_id": 1, "description": "Clear", "weight": "2.0"},
                {"id": 99, "question_id": 11, "description": "Other question", "weight": "1.0"},
            ],
        }

        current = existing_rubric({"id": 40}, props)

        assert len(current["rubric_items"]) == 5
        assert same_rubric(current, rubric)

    def test_default_items_differ(self):
        rubric, _ = normalize_rubric(CATA)
        current = {
            "rubric_items": [
                {"id": 1, "description": "Correct", "weight": "2.0"},
                {"id": 2, "description": "Incorrect", "weight": "0.0"},
            ],
            "rubric_item_groups": [],
        }

        assert not same_rubric(current, rubric)


class TestImportQuestionRubric:
    QUESTION = {"id": 40, "full_index": "4", "scoring_type": "positive", "floor": False, "ceiling": True}

    def test_requests_match_the_userscript(self):
        rubric, _ = normalize_rubric(FRQ)
        existing = {"rubric_items": [{"id": 1, "description": "Correct"}], "rubric_item_groups": []}
        created = {"rubric_item_group": {"id": 77}, "rubric_items": [{}, {}]}

        with patch.object(gs_setup, "send_json", return_value=created) as send:
            changes = import_question_rubric(Mock(), "123", self.QUESTION, rubric, existing)

        base = "/courses/123/questions/40"
        calls = [c.args[1:] for c in send.call_args_list]
        assert calls[0] == ("DELETE", f"{base}/rubric_items/1", None)
        assert calls[1] == (
            "POST",
            f"{base}/save_grading_options",
            {"question": {"id": 40, "scoring_type": "positive", "ceiling": "1", "floor": "1"}},
        )
        assert calls[2] == (
            "POST",
            f"{base}/rubric/create_group_with_items",
            {
                "rubric_item_group": {"description": "What is counted"},
                "rubric_items": [{"description": "Clear", "weight": "2"}, {"description": "Vague", "weight": "1"}],
            },
        )
        assert calls[3] == ("PUT", f"{base}/rubric_item_groups/77", {"id": 77, "mutually_exclusive": True})
        no_response = {"rubric_item": {"description": "No response", "weight": "0"}}
        assert calls[-1] == ("POST", f"{base}/rubric_items", no_response)
        assert len(changes) == len(calls) == 7

    def test_fractional_weights_are_sent_like_javascript(self):
        rubric, _ = normalize_rubric(CATA)

        with patch.object(gs_setup, "send_json", return_value=None) as send:
            import_question_rubric(Mock(), "123", self.QUESTION, rubric, None)

        body = send.call_args_list[1].args[3]
        assert body == {"rubric_item": {"description": "A **should** be selected", "weight": "0.4"}}

    def test_dry_run_sends_nothing(self):
        rubric, _ = normalize_rubric(FRQ)

        with patch.object(gs_setup, "send_json") as send:
            changes = import_question_rubric(Mock(), "123", self.QUESTION, rubric, None, dry_run=True)

        send.assert_not_called()
        assert changes[0] == "would update settings"
        assert "would set it to select one" in changes

    def test_group_without_id_in_response_stops(self):
        rubric, _ = normalize_rubric(FRQ)

        with patch.object(gs_setup, "send_json", return_value={}):
            with pytest.raises(RuntimeError, match="new group's id"):
                import_question_rubric(Mock(), "123", self.QUESTION, rubric, None)


# The body Gradescope's outline editor sent, as recorded, without its "assignment" wrapper.
OUTLINE = {
    "identification_regions": {
        "name": {"x1": 10, "x2": 40, "y1": 5, "y2": 15, "page_number": 1},
        "sid": {"x1": 60, "x2": 90, "y1": 5, "y2": 15, "page_number": 1},
    },
    "question_data": [
        {"title": "", "weight": 1, "crop_rect_list": [{"x1": 10, "x2": 90, "y1": 10, "y2": 30, "page_number": 1}]},
        {"title": "", "weight": 1, "crop_rect_list": [{"x1": 10, "x2": 90, "y1": 31, "y2": 51, "page_number": 1}]},
    ],
}

TABLE = [
    {"type": "assignment_container", "title": "Quiz 6", "url": "/courses/1/assignment_containers/428042",
     "container_id": None},
    {"type": "assignment", "title": "Quiz 6 9:30 a.m. edition", "url": "/courses/1/assignments/8976708",
     "container_id": 428042},
    {"type": "assignment", "title": "Quiz 7  -  0930", "url": "/courses/1/assignments/9000001", "container_id": None},
]


class TestOutline:
    def test_recorded_outline_is_valid(self):
        assert check_outline(OUTLINE) == []

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"question_data": []}, "non-empty array"),
            ({"question_data": [{"weight": 1, "crop_rect_list": []}]}, "crop_rect_list"),
            ({"question_data": [{"weight": "x", "crop_rect_list": OUTLINE["question_data"][0]["crop_rect_list"]}]},
             "weight"),
            ({"identification_regions": {"name": {"page_number": 1, "x1": 50, "x2": 40, "y1": 5, "y2": 15}}},
             "x1 < x2"),
        ],
    )
    def test_problems_are_reported(self, change, message):
        assert any(message in e for e in check_outline(OUTLINE | change))

    def test_indexes_number_subquestions(self):
        questions = [{}, {"children": [{}, {}]}, {}]

        assert outline_indexes(questions) == ["1", "2.1", "2.2", "3"]

    def test_config_rejects_rubric_questions_missing_from_outline(self, tmp_path):
        (tmp_path / "outline.json").write_text(json.dumps(OUTLINE))
        (tmp_path / "rubric.json").write_text('{"questions": [{"question": "1"}, {"question": "3"}]}')
        config_path = tmp_path / "gradescope.yml"
        config_path.write_text("course: 1\ntitle: Q\noutline: outline.json\nversions: {A: {rubric: rubric.json}}\n")

        with pytest.raises(ValueError, match="question 3 is not in the outline"):
            load_exam_config(config_path)

    def test_save_outline_sends_the_recorded_body(self):
        props = {"outline": [], "template": {"status_string": "processed", "page_count": 2}}

        with (
            patch.object(gs_setup, "read_outline_page", return_value=props),
            patch.object(gs_setup, "send_json") as send,
        ):
            assert save_outline(Mock(), "https://gs", "1", "8976708", OUTLINE) is True

        _, method, path, body = send.call_args.args
        assert (method, path) == ("PATCH", "/courses/1/assignments/8976708/outline/")
        assert body == {"assignment": {"identification_regions": OUTLINE["identification_regions"]},
                        "question_data": OUTLINE["question_data"]}

    def test_saved_outline_is_kept(self):
        with (
            patch.object(gs_setup, "read_outline_page", return_value={"outline": [{"id": 1}]}),
            patch.object(gs_setup, "send_json") as send,
        ):
            assert save_outline(Mock(), "https://gs", "1", "2", OUTLINE) is False

        send.assert_not_called()

    def test_region_past_last_page_is_refused(self):
        props = {"outline": [], "template": {"status_string": "processed", "page_count": 1}}
        outline = OUTLINE | {"question_data": [{"weight": 1, "crop_rect_list": [
            {"page_number": 2, "x1": 0, "x2": 100, "y1": 0, "y2": 50}]}]}

        with patch.object(gs_setup, "read_outline_page", return_value=props), patch.object(gs_setup, "send_json"):
            with pytest.raises(ValueError, match="last page"):
                save_outline(Mock(), "https://gs", "1", "2", outline)


class TestAssignments:
    def test_titles_match_loosely(self):
        assert assignments_titled(TABLE, "quiz 7 - 0930") == ["9000001"]
        assert assignments_titled(TABLE, "Quiz 6") == []  # the version set itself is not an assignment

    def test_container_of(self):
        assert container_of(TABLE, "8976708") == "428042"
        assert container_of(TABLE, "9000001") is None

    def test_version_set_request_matches_the_recording(self):
        with patch.object(gs_setup, "send_json") as send:
            create_version_set(Mock(), "1", "Quiz 6", ["8976708", "8976725"])

        assert send.call_args.args[1:] == ("POST", "/courses/1/assignment_containers")
        assert send.call_args.kwargs["form"] == [
            ("title", "Quiz 6"), ("assignment_ids[]", "8976708"), ("assignment_ids[]", "8976725")
        ]
        assert gs_setup.urlencode(send.call_args.kwargs["form"]) == (
            "title=Quiz+6&assignment_ids%5B%5D=8976708&assignment_ids%5B%5D=8976725"
        )


class TestOutlineFromPage:
    def test_saved_outline_is_nested_and_ordered(self):
        rect = {"x1": 8, "x2": 92, "y1": 8, "y2": 45, "page_number": 2}
        props = {
            "assignment": {"id_regions": {"name": OUTLINE["identification_regions"]["name"], "sid": None}},
            "outline": [
                {"id": 3, "parent_id": 1, "index": 2, "title": "b", "weight": "1.5", "crop_rect_list": [rect]},
                {"id": 1, "parent_id": None, "index": 1, "title": "Q1", "weight": "3.0", "crop_rect_list": []},
                {"id": 2, "parent_id": 1, "index": 1, "title": "a", "weight": "1.5",
                 "crop_rect_list": [rect | {"id": 99}]},
                {"id": 4, "parent_id": None, "index": 2, "title": "", "weight": "2.0", "crop_rect_list": [rect]},
            ],
        }

        outline = outline_from_page(props)

        assert outline["identification_regions"] == {"name": OUTLINE["identification_regions"]["name"]}
        assert [q["title"] for q in outline["question_data"]] == ["Q1", ""]
        assert [c["title"] for c in outline["question_data"][0]["children"]] == ["a", "b"]
        assert outline["question_data"][0]["children"][0] == {"title": "a", "weight": 1.5, "crop_rect_list": [rect]}
        assert outline_indexes(outline["question_data"]) == ["1.1", "1.2", "2"]
        assert check_outline(outline) == []


class TestGeneratedFiles:
    def test_files_next_to_the_pdf_are_used(self, tmp_path):
        (tmp_path / "q-A.qns.pdf").write_bytes(b"%PDF")
        (tmp_path / "q-A.qns.outline.json").write_text(json.dumps(OUTLINE))
        (tmp_path / "q-A.qns.rubric.json").write_text(json.dumps({"questions": [CATA]}))
        (tmp_path / "other.json").write_text(json.dumps(OUTLINE))
        config_path = tmp_path / "gradescope.yml"
        config_path.write_text(
            "course: 1\ntitle: Q\nversions:\n"
            "  A: {pdf: q-A.qns.pdf}\n"
            "  B: {pdf: q-A.qns.pdf, outline: other.json}\n"
        )

        a, b = load_exam_config(config_path).versions

        assert a.outline == (tmp_path / "q-A.qns.outline.json").resolve()
        assert a.rubric == (tmp_path / "q-A.qns.rubric.json").resolve()
        assert b.outline == (tmp_path / "other.json").resolve()
