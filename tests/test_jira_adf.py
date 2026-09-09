from __future__ import annotations

from confluence_pr_agent.jira.adf import build_story_description_adf


def _headings(doc: dict) -> list[tuple[int, str]]:
    return [
        (block["attrs"]["level"], block["content"][0]["text"])
        for block in doc["content"]
        if block["type"] == "heading"
    ]


def _heading_texts(doc: dict) -> list[str]:
    return [text for _level, text in _headings(doc)]


def test_description_without_acceptance_criteria_or_plan_has_no_extra_headings():
    doc = build_story_description_adf("Just a description.", [])
    assert _headings(doc) == []


def test_markdown_headings_and_lists_become_native_adf_nodes():
    doc = build_story_description_adf(
        "## Problem and context\n\nThe booking flow is unclear.\n\n"
        "### Implementation Plan\n\n- Update the service.\n- Add a regression test.\n\n"
        "1. Run tests.\n2. Open a PR.",
        [],
    )

    assert _headings(doc) == [(2, "Problem and context"), (3, "Implementation Plan")]
    assert [block["type"] for block in doc["content"]] == [
        "heading", "paragraph", "heading", "bulletList", "orderedList"
    ]


def test_acceptance_criteria_adds_its_own_heading_and_bullet_list():
    doc = build_story_description_adf("Desc.", ["Criterion one", "Criterion two"])
    assert "Acceptance Criteria" in _heading_texts(doc)
    bullet_lists = [b for b in doc["content"] if b["type"] == "bulletList"]
    assert len(bullet_lists) == 1
    items = [li["content"][0]["content"][0]["text"] for li in bullet_lists[0]["content"]]
    assert items == ["Criterion one", "Criterion two"]


def test_plan_summary_alone_adds_implementation_plan_heading_and_bullets():
    doc = build_story_description_adf(
        "Desc.", ["AC one"], plan_summary=["Depends on: KAN-21", "Blocks: KAN-22"]
    )
    headings = _heading_texts(doc)
    assert "Acceptance Criteria" in headings
    assert "Implementation Plan" in headings
    # Implementation Plan must come after Acceptance Criteria, not interleaved.
    assert headings.index("Acceptance Criteria") < headings.index("Implementation Plan")

    bullet_lists = [b for b in doc["content"] if b["type"] == "bulletList"]
    # AC list, then the plan_summary list -- no per-repo lists since
    # file_changes_by_repo wasn't given.
    assert len(bullet_lists) == 2
    plan_items = [li["content"][0]["content"][0]["text"] for li in bullet_lists[1]["content"]]
    assert plan_items == ["Depends on: KAN-21", "Blocks: KAN-22"]


def test_file_changes_grouped_into_one_level4_heading_and_bullet_list_per_repo():
    doc = build_story_description_adf(
        "Desc.",
        [],
        plan_summary=["Why: because the spec says so."],
        file_changes_by_repo={
            "repo-api": [
                {"file_path": "src/api/cancel.py", "change": "Validate reason.", "is_new_file": False},
                {"file_path": "src/models/appt.py", "change": "Add column.", "is_new_file": True},
            ],
            "repo-ui": [
                {"file_path": "src/forms/Cancel.tsx", "change": "Add field.", "is_new_file": True},
            ],
        },
    )
    headings = _headings(doc)
    heading_texts = [t for _l, t in headings]
    assert "Implementation Plan" in heading_texts
    level4 = [(lvl, t) for lvl, t in headings if lvl == 4]
    assert [t for _l, t in level4] == ["repo-api", "repo-ui"]

    bullet_lists = [b for b in doc["content"] if b["type"] == "bulletList"]
    # plan_summary list, then repo-api's list, then repo-ui's list.
    assert len(bullet_lists) == 3
    repo_api_items = [li["content"][0]["content"][0]["text"] for li in bullet_lists[1]["content"]]
    assert repo_api_items == [
        "src/api/cancel.py -- Validate reason.",
        "src/models/appt.py (new file) -- Add column.",
    ]
    repo_ui_items = [li["content"][0]["content"][0]["text"] for li in bullet_lists[2]["content"]]
    assert repo_ui_items == ["src/forms/Cancel.tsx (new file) -- Add field."]


def test_plan_section_omitted_when_both_empty_or_none():
    doc_none = build_story_description_adf("Desc.", [], plan_summary=None, file_changes_by_repo=None)
    doc_empty = build_story_description_adf("Desc.", [], plan_summary=[], file_changes_by_repo={})
    assert "Implementation Plan" not in _heading_texts(doc_none)
    assert "Implementation Plan" not in _heading_texts(doc_empty)
