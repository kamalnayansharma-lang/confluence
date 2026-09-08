from __future__ import annotations

from unittest.mock import AsyncMock

from confluence_pr_agent.models import (
    ChangeAgentResult,
    PipelineResult,
    PullRequestResult,
    PullRequestStatus,
    RepoChangeResult,
)
from confluence_pr_agent.pipeline import sprint_runner
from confluence_pr_agent.storage.sprint_plan_store import SprintPlan, SprintPlanPage, SprintPlanStore


def _page(page_id: str, phase: str, depends_on: list[str] | None = None, **extra) -> SprintPlanPage:
    base = SprintPlanPage(
        page_id=page_id,
        page_title=f"Page {page_id}",
        page_url=f"https://example.atlassian.net/wiki/spaces/SD/pages/{page_id}",
        page_version=1,
        page_body_html="<p>spec</p>",
        page_labels=["brd", "sprint-1", "repo-api"],
        previous_version=None,
        diff_text="(first seen)\n\nspec",
        is_first_seen=True,
        body_checksum="abc",
        predicted_labels=["repo-api"],
        applied_labels=["brd", "sprint-1", "repo-api"],
        label_gap=[],
        depends_on_page_ids=depends_on or [],
        dependency_rationale="",
        jira_issue_key=f"SD-{page_id}",
        jira_issue_url=f"https://example.atlassian.net/browse/SD-{page_id}",
        phase=phase,
    )
    base.update(extra)
    return base


def _plan(pages: list[SprintPlanPage], order: list[str]) -> SprintPlan:
    return SprintPlan(
        sprint_tag="sprint-1",
        gate_label="brd",
        space_key="SD",
        created_at="2026-01-01T00:00:00+00:00",
        pages=pages,
        order=order,
    )


def test_next_ready_page_picks_the_first_approved_page_with_no_blockers():
    plan = _plan([_page("1", "approved"), _page("2", "approved")], order=["1", "2"])
    ready = sprint_runner._next_ready_page(plan)
    assert ready is not None
    assert ready["page_id"] == "1"


def test_next_ready_page_waits_for_its_dependency_to_be_done():
    plan = _plan(
        [_page("1", "approved"), _page("2", "approved", depends_on=["1"])],
        order=["1", "2"],
    )
    ready = sprint_runner._next_ready_page(plan)
    assert ready is not None and ready["page_id"] == "1"

    # Once page 1 is done, page 2 becomes ready.
    plan["pages"][0]["phase"] = "done"
    ready = sprint_runner._next_ready_page(plan)
    assert ready is not None and ready["page_id"] == "2"


def test_next_ready_page_is_none_while_another_page_is_in_flight():
    plan = _plan([_page("1", "in_progress"), _page("2", "approved")], order=["1", "2"])
    assert sprint_runner._next_ready_page(plan) is None


def test_next_ready_page_is_none_while_another_page_is_waiting_on_merge():
    plan = _plan([_page("1", "waiting_on_merge"), _page("2", "approved")], order=["1", "2"])
    assert sprint_runner._next_ready_page(plan) is None


async def test_check_merge_status_advances_to_done_once_all_prs_merged(settings, monkeypatch):
    plan_store = SprintPlanStore(settings.sprint_plan_store_path)
    plan = _plan(
        [_page("1", "waiting_on_merge", merge_pending=[{"target_repo": "acme/api", "pr_number": 7}])],
        order=["1"],
    )
    plan_store.put(plan)

    deps = AsyncMock()
    deps.github.get_pull_request.return_value = PullRequestStatus(number=7, state="closed", merged=True)
    monkeypatch.setattr(sprint_runner, "build_deps", lambda s: deps)

    await sprint_runner._check_merge_status(settings, plan_store, plan, plan["pages"][0])

    updated = plan_store.get("sprint-1")
    assert updated["pages"][0]["phase"] == "done"


async def test_check_merge_status_stays_waiting_when_pr_still_open(settings, monkeypatch):
    plan_store = SprintPlanStore(settings.sprint_plan_store_path)
    plan = _plan(
        [_page("1", "waiting_on_merge", merge_pending=[{"target_repo": "acme/api", "pr_number": 7}])],
        order=["1"],
    )
    plan_store.put(plan)

    deps = AsyncMock()
    deps.github.get_pull_request.return_value = PullRequestStatus(number=7, state="open", merged=False)
    monkeypatch.setattr(sprint_runner, "build_deps", lambda s: deps)

    await sprint_runner._check_merge_status(settings, plan_store, plan, plan["pages"][0])

    updated = plan_store.get("sprint-1")
    assert updated["pages"][0]["phase"] == "waiting_on_merge"


async def test_advance_ready_page_moves_to_waiting_on_merge_when_a_pr_opens(settings, monkeypatch):
    plan_store = SprintPlanStore(settings.sprint_plan_store_path)
    plan = _plan([_page("1", "approved")], order=["1"])
    plan_store.put(plan)

    deps = AsyncMock()
    monkeypatch.setattr(sprint_runner, "build_deps", lambda s: deps)

    pr_result = PipelineResult(
        status="opened_pr",
        change=ChangeAgentResult(success=True, summary="Did the thing."),
        repo_results=[
            RepoChangeResult(
                target_repo="acme/api",
                status="opened_pr",
                pull_request=PullRequestResult(number=9, url="https://github.com/acme/api/pull/9", branch="x"),
            )
        ],
    )

    async def _fake_run_pipeline(page_id, deps=None, resume=None):
        assert resume is not None
        assert resume["page_id"] == "1"
        return pr_result

    monkeypatch.setattr(sprint_runner, "run_pipeline", _fake_run_pipeline)

    await sprint_runner._advance_ready_page(settings, plan_store, plan, plan["pages"][0])

    updated = plan_store.get("sprint-1")
    assert updated["pages"][0]["phase"] == "waiting_on_merge"
    assert updated["pages"][0]["merge_pending"] == [{"target_repo": "acme/api", "pr_number": 9}]


async def test_advance_ready_page_marks_failed_when_no_pr_opens(settings, monkeypatch):
    plan_store = SprintPlanStore(settings.sprint_plan_store_path)
    plan = _plan([_page("1", "approved")], order=["1"])
    plan_store.put(plan)

    deps = AsyncMock()
    monkeypatch.setattr(sprint_runner, "build_deps", lambda s: deps)

    async def _fake_run_pipeline(page_id, deps=None, resume=None):
        return PipelineResult(status="tests_failed", repo_results=[])

    monkeypatch.setattr(sprint_runner, "run_pipeline", _fake_run_pipeline)

    await sprint_runner._advance_ready_page(settings, plan_store, plan, plan["pages"][0])

    updated = plan_store.get("sprint-1")
    # "failed", not "approved" -- _next_ready_page must NOT auto-pick this
    # back up next tick (that would silently re-spend a real LLM call);
    # only an explicit human retry (the /retry endpoint) re-arms it.
    assert updated["pages"][0]["phase"] == "failed"
    assert updated["pages"][0]["last_error"]
    assert sprint_runner._next_ready_page(updated) is None


async def test_advance_ready_page_marks_failed_on_exception(settings, monkeypatch):
    plan_store = SprintPlanStore(settings.sprint_plan_store_path)
    plan = _plan([_page("1", "approved")], order=["1"])
    plan_store.put(plan)

    deps = AsyncMock()
    monkeypatch.setattr(sprint_runner, "build_deps", lambda s: deps)

    async def _fake_run_pipeline(page_id, deps=None, resume=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(sprint_runner, "run_pipeline", _fake_run_pipeline)

    await sprint_runner._advance_ready_page(settings, plan_store, plan, plan["pages"][0])

    updated = plan_store.get("sprint-1")
    assert updated["pages"][0]["phase"] == "failed"
    assert "boom" in updated["pages"][0]["last_error"]
