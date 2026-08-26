from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from confluence_pr_agent.config import get_process_config, get_settings
from confluence_pr_agent.storage.sprint_plan_store import SprintPlan, SprintPlanPage, SprintPlanStore
from confluence_pr_agent.ui.plan_sprint import _linkify_page_ids
from confluence_pr_agent.webhook.app import app


def test_linkify_page_ids_wraps_a_known_page_id_in_a_link():
    pages_by_id = {
        "5275661": {"page_url": "https://example.atlassian.net/wiki/spaces/SD/pages/5275661", "page_title": "Care Scheduler Platform"}
    }
    html_out = _linkify_page_ids("This page depends on 5275661 because it refers to the same standard.", pages_by_id)
    assert '<a href="https://example.atlassian.net/wiki/spaces/SD/pages/5275661"' in html_out
    assert 'title="Care Scheduler Platform"' in html_out
    assert ">5275661</a>" in html_out


def test_linkify_page_ids_leaves_unknown_numbers_alone():
    # A number that isn't a known page id in this plan (e.g. "200" in "1-200
    # characters") must never become a link -- only ids this plan actually
    # knows about.
    html_out = _linkify_page_ids("The reason must be 1-200 characters.", pages_by_id={})
    assert "<a " not in html_out
    assert "1-200 characters" in html_out


def test_linkify_page_ids_escapes_untrusted_text():
    html_out = _linkify_page_ids("<script>alert(1)</script> depends on 999999999", pages_by_id={})
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


@pytest.fixture(autouse=True)
def _isolated_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEFAULT_USER", "testuser")
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", "")
    get_process_config.cache_clear()
    get_settings.cache_clear()
    yield
    get_process_config.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


def _seed_plan() -> None:
    settings = get_settings("testuser")
    store = SprintPlanStore(settings.sprint_plan_store_path)
    store.put(
        SprintPlan(
            sprint_tag="sprint-24",
            gate_label="brd",
            space_key="SD",
            created_at="2026-01-01T00:00:00+00:00",
            order=["1001", "1002"],
            pages=[
                SprintPlanPage(
                    page_id="1001",
                    page_title="Cancellation reason",
                    page_url="https://example.atlassian.net/wiki/spaces/SD/pages/1001",
                    page_version=1,
                    page_body_html="<p>spec</p>",
                    page_labels=["brd", "sprint-24", "repo-api"],
                    previous_version=None,
                    diff_text="(first seen)\n\nspec",
                    is_first_seen=True,
                    body_checksum="abc",
                    predicted_labels=["repo-api", "repo-ui"],
                    applied_labels=["brd", "sprint-24", "repo-api"],
                    label_gap=["repo-ui"],
                    depends_on_page_ids=[],
                    dependency_rationale="",
                    phase="planned",
                ),
                SprintPlanPage(
                    page_id="1002",
                    page_title="Reminder toggle",
                    page_url="https://example.atlassian.net/wiki/spaces/SD/pages/1002",
                    page_version=1,
                    page_body_html="<p>spec 2</p>",
                    page_labels=["brd", "sprint-24", "repo-ui"],
                    previous_version=None,
                    diff_text="(first seen)\n\nspec 2",
                    is_first_seen=True,
                    body_checksum="def",
                    predicted_labels=["repo-ui"],
                    applied_labels=["brd", "sprint-24", "repo-ui"],
                    label_gap=[],
                    depends_on_page_ids=["1001"],
                    dependency_rationale="Reuses the reason field page 1001 introduces.",
                    jira_issue_key="SD-2",
                    jira_issue_url="https://example.atlassian.net/browse/SD-2",
                    phase="confirmed",
                ),
            ],
        )
    )


def test_plan_sprint_index_renders_with_no_plans(client):
    resp = client.get("/ui/plan-sprint")
    assert resp.status_code == 200
    assert "Plan a Sprint" in resp.text


def test_plan_sprint_index_lists_an_existing_plan(client):
    _seed_plan()
    resp = client.get("/ui/plan-sprint")
    assert resp.status_code == 200
    assert "sprint-24" in resp.text
    assert "1 planned" in resp.text
    assert "1 confirmed" in resp.text


def test_plan_sprint_detail_renders_gap_badge_and_dependency(client):
    _seed_plan()
    resp = client.get("/ui/plan-sprint/sprint-24")
    assert resp.status_code == 200
    assert "Cancellation reason" in resp.text
    assert "repo-ui" in resp.text  # the missing-label gap
    assert "Reminder toggle" in resp.text
    # "1001" is itself a known page_id in this plan, so the rationale text
    # gets it wrapped in a link (see _linkify_page_ids) -- the surrounding
    # sentence must still be there, just with 1001 as an anchor, not bare text.
    assert "Reuses the reason field page" in resp.text
    assert "introduces." in resp.text
    assert '<a href="https://example.atlassian.net/wiki/spaces/SD/pages/1001"' in resp.text
    assert ">1001</a>" in resp.text
    assert "SD-2" in resp.text
    # Order matters: the dependent (Reminder toggle) must render after its
    # dependency (Cancellation reason) in the ordered table.
    assert resp.text.index("Cancellation reason") < resp.text.index("Reminder toggle")

    # The reverse relation ("Blocks") must be visible too, not just
    # "Depends on" -- computed server-side in plan_sprint_detail, not just
    # implied by reading the other row.
    assert "Blocked by" in resp.text
    assert "Blocks" in resp.text


def test_plan_sprint_detail_distinguishes_already_applied_predictions_from_gaps(client):
    """Page 1001's predicted_labels=["repo-api", "repo-ui"], applied_labels
    includes "repo-api" already, label_gap=["repo-ui"] only -- repo-api
    must render as a confirmed (checkmark) prediction, repo-ui as the
    actual gap (warning), not both looking identical in the Predicted
    column.
    """
    _seed_plan()
    resp = client.get("/ui/plan-sprint/sprint-24")
    assert resp.status_code == 200
    assert "&#10003; repo-api" in resp.text  # already applied -- confirmed, not a gap
    assert "&#9888;&#65039; repo-ui" in resp.text  # predicted but missing -- the real gap


def test_plan_sprint_detail_missing_plan_redirects_with_error(client):
    resp = client.get("/ui/plan-sprint/does-not-exist", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_plan_sprint_detail_each_row_gets_the_checkbox_matching_its_phase(client):
    """Page 1001 is still "planned" (no story yet -- Confirm hasn't run) --
    it gets a row-select-planned checkbox tied to confirm-selected-form.
    Page 1002 is "confirmed" -- it gets a row-select-confirmed checkbox
    tied to approve-selected-form. Neither gets the other's checkbox.
    """
    _seed_plan()
    resp = client.get("/ui/plan-sprint/sprint-24")
    assert resp.status_code == 200

    assert 'class="row-select-planned" name="page_ids" value="1001" form="confirm-selected-form"' in resp.text
    assert 'class="row-select-confirmed" name="page_ids" value="1002" form="approve-selected-form"' in resp.text
    # Cross-check: 1001 never gets the confirmed-style checkbox, 1002 never gets the planned-style one.
    assert 'value="1001" form="approve-selected-form"' not in resp.text
    assert 'value="1002" form="confirm-selected-form"' not in resp.text

    assert 'id="confirm-selected-form"' in resp.text
    assert 'id="approve-selected-form"' in resp.text
    assert 'id="pager-controls"' in resp.text


async def test_approve_one_page_success(monkeypatch):
    from unittest.mock import AsyncMock

    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    jira = AsyncMock()
    jira.transition_issue.return_value = True
    plan = {
        "pages": [
            {"page_id": "1002", "page_title": "Reminder toggle", "jira_issue_key": "SD-2", "phase": "confirmed"}
        ]
    }

    error = await plan_sprint_module._approve_one_page(jira, plan, "1002", "Approved")

    assert error is None
    assert plan["pages"][0]["phase"] == "approved"
    jira.transition_issue.assert_awaited_once_with("SD-2", "Approved")


async def test_approve_one_page_no_story_yet():
    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    plan = {"pages": [{"page_id": "1001", "page_title": "Cancellation reason", "phase": "planned"}]}
    error = await plan_sprint_module._approve_one_page(None, plan, "1001", "Approved")
    assert error is not None
    assert "Confirm the plan first" in error


async def test_approve_one_page_no_matching_transition():
    from unittest.mock import AsyncMock

    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    jira = AsyncMock()
    jira.transition_issue.return_value = False
    plan = {
        "pages": [{"page_id": "1002", "page_title": "Reminder toggle", "jira_issue_key": "SD-2", "phase": "confirmed"}]
    }

    error = await plan_sprint_module._approve_one_page(jira, plan, "1002", "Approved")

    assert error is not None
    assert "no transition named" in error
    assert plan["pages"][0]["phase"] == "confirmed"  # unchanged on failure


def test_plan_sprint_approve_selected_batch_updates_only_selected_pages(client, monkeypatch):
    """Full route test: two confirmed pages, only one selected -- confirms
    the batch endpoint calls Jira once per selected page id (not all
    confirmed pages), and only the selected one's phase advances.
    """
    from unittest.mock import AsyncMock

    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    settings = get_settings("testuser")
    settings.jira_approved_status_name = "Approved"
    store = SprintPlanStore(settings.sprint_plan_store_path)
    store.put(
        SprintPlan(
            sprint_tag="sprint-batch",
            gate_label="brd",
            space_key="SD",
            created_at="2026-01-01T00:00:00+00:00",
            order=["2001", "2002"],
            pages=[
                SprintPlanPage(
                    page_id="2001", page_title="Story A", page_url="https://x/2001", page_version=1,
                    page_body_html="<p>a</p>", page_labels=["brd"], previous_version=None, diff_text="a",
                    is_first_seen=True, body_checksum="a", predicted_labels=[], applied_labels=[], label_gap=[],
                    depends_on_page_ids=[], dependency_rationale="", jira_issue_key="SD-101",
                    jira_issue_url="https://x/browse/SD-101", phase="confirmed",
                ),
                SprintPlanPage(
                    page_id="2002", page_title="Story B", page_url="https://x/2002", page_version=1,
                    page_body_html="<p>b</p>", page_labels=["brd"], previous_version=None, diff_text="b",
                    is_first_seen=True, body_checksum="b", predicted_labels=[], applied_labels=[], label_gap=[],
                    depends_on_page_ids=[], dependency_rationale="", jira_issue_key="SD-102",
                    jira_issue_url="https://x/browse/SD-102", phase="confirmed",
                ),
            ],
        )
    )

    fake_jira = AsyncMock()
    fake_jira.transition_issue.return_value = True

    class _FakeJiraClient:
        def __new__(cls, *args, **kwargs):
            return fake_jira

    monkeypatch.setattr(plan_sprint_module, "JiraClient", _FakeJiraClient)

    resp = client.post(
        "/ui/plan-sprint/sprint-batch/approve-selected",
        data={"page_ids": ["2001"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers.get("location", "")

    fake_jira.transition_issue.assert_awaited_once_with("SD-101", "Approved")
    updated = store.get("sprint-batch")
    pages_by_id = {p["page_id"]: p for p in updated["pages"]}
    assert pages_by_id["2001"]["phase"] == "approved"
    assert pages_by_id["2002"]["phase"] == "confirmed"  # untouched -- not selected


def test_plan_sprint_approve_selected_rejects_empty_selection(client):
    _seed_plan()
    get_settings("testuser").jira_approved_status_name = "Approved"

    resp = client.post(
        "/ui/plan-sprint/sprint-24/approve-selected", data={}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert "No%20stories%20selected" in resp.headers["location"]


def _planned_page(page_id: str, title: str) -> SprintPlanPage:
    return SprintPlanPage(
        page_id=page_id, page_title=title, page_url=f"https://x/{page_id}", page_version=1,
        page_body_html=f"<p>{title}</p>", page_labels=["brd"], previous_version=None, diff_text=title,
        is_first_seen=True, body_checksum=page_id, predicted_labels=[], applied_labels=[], label_gap=[],
        depends_on_page_ids=[], dependency_rationale="", phase="planned",
    )


async def test_confirm_one_page_creates_a_new_story_when_none_exists(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from confluence_pr_agent.jira.story_writer import JiraStoryContent
    from confluence_pr_agent.models import JiraIssueResult
    from confluence_pr_agent.storage.page_store import PageStore
    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    page_store = PageStore(tmp_path / "store.json")
    jira = AsyncMock()
    jira.create_issue.return_value = JiraIssueResult(key="SD-9", url="https://x/browse/SD-9")

    async def _fake_story(settings, diff):
        return JiraStoryContent(summary="s", description="d", acceptance_criteria=[])

    monkeypatch.setattr(plan_sprint_module, "generate_story_content", _fake_story)

    sp = _planned_page("3001", "New Story")
    settings = get_settings("testuser")
    settings.jira_base_url = "https://x.atlassian.net"
    settings.jira_project_key = "SD"
    settings.jira_issue_type = "Story"
    error = await plan_sprint_module._confirm_one_page(settings, jira, page_store, sp, "sprint-x")

    assert error is None
    assert sp["phase"] == "confirmed"
    assert sp["jira_issue_key"] == "SD-9"
    jira.create_issue.assert_awaited_once()
    jira.add_comment.assert_awaited_once()
    # remember_jira_issue is a merge, not an insert (see
    # storage/page_store.py) -- a page never seen by PageStore before (no
    # prior full record from a real pipeline run) correctly stays absent,
    # not partially/incorrectly recorded.
    assert page_store.get("3001") is None


async def test_confirm_one_page_reuses_an_existing_open_story(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from confluence_pr_agent.models import JiraIssueStatus
    from confluence_pr_agent.storage.page_store import PageStore, StoredPage
    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    page_store = PageStore(tmp_path / "store.json")
    page_store.put(
        StoredPage(
            page_id="3002", title="Existing", version=1, body_html="<p>x</p>", body_checksum="x",
            url="https://x/3002", jira_issue_key="SD-8",
        )
    )
    jira = AsyncMock()
    jira.get_issue_status.return_value = JiraIssueStatus(key="SD-8", status_name="To Do", status_category="new")

    sp = _planned_page("3002", "Existing")
    settings = get_settings("testuser")
    settings.jira_base_url = "https://x.atlassian.net"
    settings.jira_project_key = "SD"
    settings.jira_issue_type = "Story"
    error = await plan_sprint_module._confirm_one_page(settings, jira, page_store, sp, "sprint-x")

    assert error is None
    assert sp["phase"] == "confirmed"
    assert sp["jira_issue_key"] == "SD-8"
    jira.create_issue.assert_not_awaited()  # reused, not duplicated


async def test_confirm_one_page_returns_error_and_leaves_phase_unchanged_on_failure(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from confluence_pr_agent.jira.story_writer import JiraStoryContent
    from confluence_pr_agent.storage.page_store import PageStore
    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    page_store = PageStore(tmp_path / "store.json")
    jira = AsyncMock()
    jira.create_issue.side_effect = RuntimeError("Jira is down")

    async def _fake_story(settings, diff):
        return JiraStoryContent(summary="s", description="d", acceptance_criteria=[])

    monkeypatch.setattr(plan_sprint_module, "generate_story_content", _fake_story)

    sp = _planned_page("3003", "Broken")
    settings = get_settings("testuser")
    settings.jira_base_url = "https://x.atlassian.net"
    settings.jira_project_key = "SD"
    settings.jira_issue_type = "Story"
    error = await plan_sprint_module._confirm_one_page(settings, jira, page_store, sp, "sprint-x")

    assert error is not None
    assert "Jira is down" in error
    assert sp["phase"] == "planned"  # unchanged on failure


def test_plan_sprint_confirm_selected_only_confirms_chosen_pages(client, monkeypatch):
    """Two planned pages, only one selected -- confirms the batch endpoint
    calls Jira once for the selected page only, and the other stays
    "planned" rather than being swept up too.
    """
    from unittest.mock import AsyncMock

    from confluence_pr_agent.jira.story_writer import JiraStoryContent
    from confluence_pr_agent.models import JiraIssueResult
    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    settings = get_settings("testuser")
    store = SprintPlanStore(settings.sprint_plan_store_path)
    store.put(
        SprintPlan(
            sprint_tag="sprint-confirm-batch", gate_label="brd", space_key="SD",
            created_at="2026-01-01T00:00:00+00:00", order=["4001", "4002"],
            pages=[_planned_page("4001", "Story A"), _planned_page("4002", "Story B")],
        )
    )

    fake_jira = AsyncMock()
    fake_jira.create_issue.return_value = JiraIssueResult(key="SD-201", url="https://x/browse/SD-201")

    class _FakeJiraClient:
        def __new__(cls, *args, **kwargs):
            return fake_jira

    async def _fake_story(settings, diff):
        return JiraStoryContent(summary="s", description="d", acceptance_criteria=[])

    monkeypatch.setattr(plan_sprint_module, "JiraClient", _FakeJiraClient)
    monkeypatch.setattr(plan_sprint_module, "generate_story_content", _fake_story)

    resp = client.post(
        "/ui/plan-sprint/sprint-confirm-batch/confirm-selected",
        data={"page_ids": ["4001"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers.get("location", "")

    fake_jira.create_issue.assert_awaited_once()
    updated = store.get("sprint-confirm-batch")
    pages_by_id = {p["page_id"]: p for p in updated["pages"]}
    assert pages_by_id["4001"]["phase"] == "confirmed"
    assert pages_by_id["4001"]["jira_issue_key"] == "SD-201"
    assert pages_by_id["4002"]["phase"] == "planned"  # untouched -- not selected


def test_plan_sprint_confirm_selected_rejects_empty_selection(client):
    _seed_plan()
    resp = client.post(
        "/ui/plan-sprint/sprint-24/confirm-selected", data={}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert "No%20stories%20selected" in resp.headers["location"]


def test_group_file_changes_groups_by_repo_label_preserving_order():
    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    file_changes = [
        {"repo_label": "repo-api", "file_path": "src/api/cancel.py", "change": "Validate reason.", "is_new_file": False},
        {"repo_label": "repo-ui", "file_path": "src/forms/cancel.tsx", "change": "Add field.", "is_new_file": True},
        {"repo_label": "repo-api", "file_path": "src/models/appt.py", "change": "Add column.", "is_new_file": False},
    ]

    grouped = plan_sprint_module._group_file_changes(file_changes)

    assert list(grouped.keys()) == ["repo-api", "repo-ui"]
    assert len(grouped["repo-api"]) == 2
    assert len(grouped["repo-ui"]) == 1
    assert grouped["repo-api"][0]["file_path"] == "src/api/cancel.py"


def test_build_plan_summary_lines_includes_repo_scope_order_and_dependency():
    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    dependency = _planned_page("5001", "Schema change")
    dependency["jira_issue_key"] = "KAN-50"
    dependent = _planned_page("5002", "API change")
    dependent.update(
        depends_on_page_ids=["5001"],
        dependency_rationale="Needs the new column from 5001.",
        predicted_labels=["repo-api"],
        rationale="The spec requires a reason to be captured and validated server-side.",
        cross_repo_impact="The UI will need a form field for this, or requests will fail validation.",
    )
    plan = {"sprint_tag": "sprint-x", "order": ["5001", "5002"], "pages": [dependency, dependent]}
    keys_by_page = {"5001": "KAN-50", "5002": None}

    lines = plan_sprint_module._build_plan_summary_lines(dependent, plan, keys_by_page)

    assert any("Why: The spec requires a reason" in line for line in lines)
    assert any("Cross-repo impact: The UI will need a form field" in line for line in lines)
    assert any("Repo(s) this story is expected to touch: repo-api" in line for line in lines)
    assert any("Sprint position: step 2 of 2" in line for line in lines)
    assert any("Depends on: KAN-50" in line and "Needs the new column from 5001." in line for line in lines)


def test_build_plan_summary_lines_flags_unresolved_dependency_by_title():
    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    dependency = _planned_page("6001", "Not yet confirmed dep")
    dependent = _planned_page("6002", "Dependent story")
    dependent["depends_on_page_ids"] = ["6001"]
    plan = {"sprint_tag": "sprint-y", "order": ["6001", "6002"], "pages": [dependency, dependent]}
    keys_by_page = {"6001": None, "6002": None}  # 6001 has no story yet

    lines = plan_sprint_module._build_plan_summary_lines(dependent, plan, keys_by_page)

    assert any('"Not yet confirmed dep" (not yet confirmed' in line for line in lines)


async def test_write_implementation_plan_sections_updates_every_confirmed_page(monkeypatch):
    from unittest.mock import AsyncMock

    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    sp = _planned_page("7001", "Story with plan")
    sp["phase"] = "confirmed"
    sp["jira_issue_key"] = "KAN-70"
    sp["story_description"] = "Some description."
    sp["story_acceptance_criteria"] = ["AC one"]
    sp["file_changes"] = [
        {"repo_label": "repo-api", "file_path": "src/api/cancel.py", "change": "Do the thing.", "is_new_file": False}
    ]
    plan = {"sprint_tag": "sprint-z", "order": ["7001"], "pages": [sp]}

    jira = AsyncMock()
    await plan_sprint_module._write_implementation_plan_sections(jira, plan)

    jira.update_description.assert_awaited_once()
    call = jira.update_description.await_args
    assert call.args[0] == "KAN-70"
    assert call.args[1] == "Some description."
    assert call.args[2] == ["AC one"]
    assert "repo-api" in call.kwargs["file_changes_by_repo"]
    assert call.kwargs["file_changes_by_repo"]["repo-api"][0]["file_path"] == "src/api/cancel.py"


async def test_write_implementation_plan_sections_skips_pages_without_a_story(monkeypatch):
    from unittest.mock import AsyncMock

    from confluence_pr_agent.ui import plan_sprint as plan_sprint_module

    sp = _planned_page("7002", "Not confirmed yet")  # no jira_issue_key
    plan = {"sprint_tag": "sprint-z", "order": ["7002"], "pages": [sp]}

    jira = AsyncMock()
    await plan_sprint_module._write_implementation_plan_sections(jira, plan)

    jira.update_description.assert_not_awaited()


def test_plan_sprint_detail_renders_grouped_implementation_plan(client):
    """End-to-end template check: file_changes render grouped by repo_label
    (a heading per repo, its own bullet list under it), inside a <details>
    disclosure under the story title, not bloating the table by default.
    """
    settings = get_settings("testuser")
    store = SprintPlanStore(settings.sprint_plan_store_path)
    page = _planned_page("8001", "Story with a real plan")
    page.update(
        file_changes=[
            {"repo_label": "repo-api", "file_path": "src/api/booking.py", "change": "Add validation.", "is_new_file": False},
            {"repo_label": "repo-ui", "file_path": "src/forms/Booking.tsx", "change": "Add a field.", "is_new_file": True},
        ],
        rationale="This is required because the spec says so.",
        cross_repo_impact="The UI repo also needs a corresponding change.",
    )
    store.put(
        SprintPlan(
            sprint_tag="sprint-details", gate_label="brd", space_key="SD",
            created_at="2026-01-01T00:00:00+00:00", order=["8001"], pages=[page],
        )
    )

    resp = client.get("/ui/plan-sprint/sprint-details")
    assert resp.status_code == 200
    assert "Implementation plan (best-effort)" in resp.text
    # Grouped: each repo appears once as its own heading, not interleaved.
    assert resp.text.count(">repo-api<") == 1
    assert resp.text.count(">repo-ui<") == 1
    assert "src/api/booking.py" in resp.text
    assert "src/forms/Booking.tsx" in resp.text
    assert "(new file)" in resp.text
    assert "This is required because the spec says so." in resp.text
    assert "The UI repo also needs a corresponding change." in resp.text
