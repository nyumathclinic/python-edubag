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
    delete_assignment,
    delete_assignment_container,
    existing_rubric,
    find_rows,
    import_assignment_rubric,
    import_question_rubric,
    is_default_rubric,
    leaf_questions,
    load_exam_config,
    match_rubric_file,
    normalize_rubric,
    outline_from_page,
    outline_indexes,
    same_rubric,
    save_outline,
    with_parent_weights,
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

    def test_student_visibility(self, tmp_path):
        config_path = tmp_path / "gradescope.yml"
        config_path.write_text("course: 1\ntitle: Quiz 6\nversions: {A: {}}\n")
        assert load_exam_config(config_path).student_visibility is None

        config_path.write_text("course: 1\ntitle: Quiz 6\nstudent_visibility: applied\nversions: {A: {}}\n")
        assert load_exam_config(config_path).student_visibility == "applied"

        config_path.write_text("course: 1\ntitle: Quiz 6\nstudent_visibility: some\nversions: {A: {}}\n")
        with pytest.raises(ValueError, match="'student_visibility' must be one of all, applied"):
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


class TestDefaultRubric:
    """Gradescope gives every new question one item, "Correct", worth 0."""

    QUESTION = {"id": 40, "full_index": "4", "title": "Q4", "scoring_type": "negative", "floor": True,
                "ceiling": True}
    DEFAULT = {"id": 7, "question_id": 40, "description": "Correct", "weight": "0.0"}

    @pytest.mark.parametrize(
        ("items", "groups", "expected"),
        [
            ([DEFAULT], [], True),
            ([DEFAULT | {"weight": "2.0"}], [], False),
            ([DEFAULT | {"description": "No response"}], [], False),
            ([DEFAULT, DEFAULT | {"id": 8}], [], False),
            ([DEFAULT], [{"id": 1, "description": "Group"}], False),
        ],
    )
    def test_is_default_rubric(self, items, groups, expected):
        assert is_default_rubric({"rubric_items": items, "rubric_item_groups": groups}) is expected

    def _import(self, items: list[dict]) -> tuple[dict, Mock]:
        props = {"questions": [self.QUESTION], "rubric_items": items, "rubric_item_groups": []}
        page = Mock()
        page.url = "https://gs/courses/1/assignments/2/rubric/edit"
        created = {"rubric_item_group": {"id": 77}, "rubric_items": [{}, {}]}
        with (
            patch.object(gs_setup, "read_rubric_page", return_value=props),
            patch.object(gs_setup, "send_json", return_value=created) as send,
        ):
            result = import_assignment_rubric(page, "https://gs", "1", "2", {"questions": [FRQ]})
        return result, send

    def test_default_rubric_is_replaced_without_asking(self):
        result, send = self._import([self.DEFAULT])

        assert result["questions"][0]["status"] == "imported"
        assert send.call_args_list[0].args[1:] == ("DELETE", "/courses/1/questions/40/rubric_items/7", None)

    def test_other_rubric_is_left_alone(self):
        result, send = self._import([self.DEFAULT | {"description": "My item", "weight": "1.0"}])

        assert result["questions"][0]["status"] == "conflict"
        send.assert_not_called()


class TestParentWeights:
    def test_parent_gets_the_sum_of_its_parts(self):
        questions = [
            {"title": "Q1", "weight": 10, "crop_rect_list": []},
            {"title": "Q2", "children": [
                {"title": "(a)", "weight": 2, "crop_rect_list": []},
                {"title": "(b)", "children": [{"title": "i", "weight": 1.5}, {"title": "ii", "weight": 1.5}]},
            ]},
        ]

        out = with_parent_weights(questions)

        assert out[0] == questions[0]
        assert out[1]["weight"] == 5
        assert out[1]["children"][1]["weight"] == 3
        assert "weight" not in questions[1]  # the input is not changed

    def test_a_given_parent_weight_is_kept(self):
        questions = [{"title": "Q", "weight": 8, "children": [{"title": "(a)", "weight": 2}]}]

        assert with_parent_weights(questions)[0]["weight"] == 8

    def test_save_outline_sends_parent_weights(self):
        props = {"outline": [], "template": {"status_string": "processed", "page_count": 2}}
        rect = OUTLINE["question_data"][0]["crop_rect_list"]
        outline = OUTLINE | {"question_data": [
            {"title": "Q", "children": [{"title": "(a)", "weight": 2, "crop_rect_list": rect},
                                        {"title": "(b)", "weight": 3, "crop_rect_list": rect}]},
        ]}

        with (
            patch.object(gs_setup, "read_outline_page", return_value=props),
            patch.object(gs_setup, "send_json") as send,
        ):
            save_outline(Mock(), "https://gs", "1", "2", outline)

        assert send.call_args.args[3]["question_data"][0]["weight"] == 5


class TestDelete:
    TABLE = [
        {"type": "assignment_container", "title": "Chapter 7 Test", "url": "/courses/1/assignment_containers/430",
         "num_active_submissions": 0},
        {"type": "assignment", "title": "Chapter 7 Test - A", "url": "/courses/1/assignments/901",
         "container_id": 430, "num_active_submissions": 0},
        {"type": "assignment", "title": "Chapter 7 Test - B", "url": "/courses/1/assignments/902",
         "container_id": 430, "num_active_submissions": 3},
        {"type": "assignment", "title": "Problem Set 7", "url": "/courses/1/assignments/800"},
    ]

    def test_rows_are_found_by_id_or_title(self):
        found, missing = find_rows(self.TABLE, "assignment", ["chapter 7 test -  a", "902", "800", "999", "901"])

        assert [(r["id"], r["submissions"]) for r in found] == [("901", 0), ("902", 3), ("800", 0)]
        assert missing == ["999"]

    def test_kinds_are_kept_apart(self):
        assert find_rows(self.TABLE, "assignment", ["Chapter 7 Test"]) == ([], ["Chapter 7 Test"])
        found, _ = find_rows(self.TABLE, "assignment_container", ["Chapter 7 Test"])
        assert found == [{"id": "430", "title": "Chapter 7 Test", "submissions": 0}]

    def test_ambiguous_titles_are_refused(self):
        table = self.TABLE + [self.TABLE[1] | {"url": "/courses/1/assignments/903"}]

        with pytest.raises(LookupError, match="More than one assignment"):
            find_rows(table, "assignment", ["Chapter 7 Test - A"])

    def test_requests_match_the_delete_links(self):
        page = Mock()
        page.url = "https://www.gradescope.com/courses/1/assignments"
        page.locator.return_value.first.get_attribute.return_value = "tok"
        page.request.fetch.return_value.status = 302

        delete_assignment_container(page, "1", "430")
        delete_assignment(page, "1", "901")

        calls = page.request.fetch.call_args_list
        assert [c.args[0] for c in calls] == [
            "https://www.gradescope.com/courses/1/assignment_containers/430",
            "https://www.gradescope.com/courses/1/assignments/901",
        ]
        for c in calls:
            assert c.kwargs["method"] == "POST"
            assert c.kwargs["data"] == "_method=delete&authenticity_token=tok"
            assert c.kwargs["headers"]["X-CSRF-Token"] == "tok"
            assert c.kwargs["max_redirects"] == 0

    def test_error_response_is_raised(self):
        page = Mock()
        page.url = "https://www.gradescope.com/courses/1/assignments"
        page.request.fetch.return_value.status = 500
        page.request.fetch.return_value.text.return_value = '{"error":"Internal Server Error"}'

        with pytest.raises(RuntimeError, match="returned 500"):
            delete_assignment(page, "1", "901")

    def _run(self, tmp_path, method: str, wanted: list[str], **kwargs):
        from edubag.gradescope import client as gs_client

        client = gs_client.GradescopeClient(base_url="https://gs", auth_state_path=tmp_path / "auth.json")
        page = Mock()
        page.url = "https://gs/courses/1/assignments"
        playwright = Mock()
        playwright.chromium.launch.return_value.new_context.return_value.new_page.return_value = page
        with (
            patch.object(gs_client, "sync_playwright") as sync,
            patch.object(gs_client.GradescopeClient, "_with_reauth", lambda self, session, headless: session()),
            patch.object(gs_client.GradescopeClient, "_read_assignments_table", return_value=self.TABLE),
            patch.object(gs_client, "delete_assignment") as del_assignment,
            patch.object(gs_client, "delete_assignment_container") as del_container,
        ):
            sync.return_value.__enter__.return_value = playwright
            result = getattr(client, method)("1", wanted, **kwargs)
        return result, del_assignment, del_container

    def test_submissions_are_refused_without_force(self, tmp_path):
        with pytest.raises(RuntimeError, match=r"Chapter 7 Test - B \(902\) has submissions"):
            self._run(tmp_path, "delete_assignments", ["901", "902"])

    def test_dry_run_and_cancel_delete_nothing(self, tmp_path):
        result, del_assignment, _ = self._run(tmp_path, "delete_assignments", ["901"], dry_run=True)
        assert result["status"] == "dry run"
        result, del_assignment, _ = self._run(tmp_path, "delete_assignments", ["901"], confirm=lambda plan: False)
        assert result["status"] == "cancelled"
        del_assignment.assert_not_called()

    def test_nothing_found(self, tmp_path):
        result, del_assignment, _ = self._run(tmp_path, "delete_assignments", ["999"])
        assert (result["status"], result["missing"]) == ("nothing to delete", ["999"])
        del_assignment.assert_not_called()

    def test_forced_deletion(self, tmp_path):
        result, del_assignment, _ = self._run(tmp_path, "delete_assignments", ["901", "902", "999"], force=True,
                                              confirm=lambda plan: True)

        assert result["status"] == "deleted"
        assert result["missing"] == ["999"]
        assert [c.args[1:] for c in del_assignment.call_args_list] == [("1", "901"), ("1", "902")]

    def test_container(self, tmp_path):
        result, _, del_container = self._run(tmp_path, "delete_assignment_containers", ["Chapter 7 Test"],
                                             confirm=lambda plan: True)

        assert result["status"] == "deleted"
        assert del_container.call_args.args[1:] == ("1", "430")


class TestOutlineFromNestedPage:
    def test_children_listed_under_their_parent(self):
        rect = {"x1": 8.8, "x2": 91.2, "y1": 18.2, "y2": 33.5, "page_number": 17}
        props = {"outline": [
            {"id": 1, "type": "FreeResponseQuestion", "parent_id": None, "index": 1, "title": "Question 18",
             "weight": "10.0", "crop_rect_list": [rect]},
            {"id": 2, "type": "QuestionGroup", "parent_id": None, "index": 2, "title": "Question 19",
             "weight": "10.0", "children": [
                 {"id": 4, "parent_id": 2, "index": 2, "title": "(b)", "weight": "3.0", "crop_rect_list": [rect]},
                 {"id": 3, "parent_id": 2, "index": 1, "title": "(a)", "weight": "2.0", "crop_rect_list": [rect]},
             ]},
        ]}

        outline = outline_from_page(props)

        q19 = outline["question_data"][1]
        assert [c["title"] for c in q19["children"]] == ["(a)", "(b)"]
        assert q19["children"][0] == {"title": "(a)", "weight": 2.0, "crop_rect_list": [rect]}
        assert "crop_rect_list" not in q19
        assert check_outline(outline) == []
