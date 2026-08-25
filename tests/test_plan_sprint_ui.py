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


def test_plan_sprint_detail_only_confirmed_pages_get_a_checkbox(client):
    """Page 1001 is still "planned" (no story yet -- Confirm hasn't run),
    page 1002 is "confirmed" -- only 1002 should be selectable for batch
    approval.
    """
    _seed_plan()
    resp = client.get("/ui/plan-sprint/sprint-24")
    assert resp.status_code == 200
    assert 'name="page_ids" value="1002"' in resp.text
    assert 'name="page_ids" value="1001"' not in resp.text
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
