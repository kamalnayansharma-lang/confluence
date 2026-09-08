from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from confluence_pr_agent.confluence.diff import _to_plain_text, compute_checksum
from confluence_pr_agent.jira.story_writer import JiraStoryContent
from confluence_pr_agent.models import (
    ChangeAgentResult,
    JiraIssueResult,
    JiraIssueStatus,
    JudgeCriterion,
    JudgeResult,
    PageSnapshot,
    PullRequestResult,
    PullRequestStatus,
    RepoTestResult,
)
from confluence_pr_agent.pipeline import orchestrator
from confluence_pr_agent.pipeline.orchestrator import PipelineDeps, build_deps, run_pipeline
from confluence_pr_agent.storage.page_store import PageStore, StoredPage
from confluence_pr_agent.storage.pending_approval_store import PendingApproval, PendingApprovalStore
from confluence_pr_agent.storage.run_store import RunStore
from confluence_pr_agent.storage.sprint_plan_store import SprintPlanStore


def _page(version: int, body: str = "<p>spec</p>", labels: list[str] | None = None) -> PageSnapshot:
    return PageSnapshot(
        page_id="123456",
        title="Checkout Flow Spec",
        version=version,
        body_html=body,
        url="https://example.atlassian.net/wiki/spaces/SD/pages/123456",
        labels=labels or [],
    )


def _stored(
    version: int,
    body_html: str,
    url: str,
    *,
    open_pr_number: int | None = None,
    open_pr_branch: str | None = None,
    jira_issue_key: str | None = None,
) -> StoredPage:
    return StoredPage(
        page_id="123456",
        title="Checkout Flow Spec",
        version=version,
        body_html=body_html,
        body_checksum=compute_checksum(_to_plain_text(body_html)),
        url=url,
        open_pr_number=open_pr_number,
        open_pr_branch=open_pr_branch,
        jira_issue_key=jira_issue_key,
    )


def _make_deps(settings, *, page: PageSnapshot) -> PipelineDeps:
    confluence = AsyncMock()
    confluence.fetch_page.return_value = page

    git = AsyncMock()
    git.has_changes.return_value = True
    git.changed_files.return_value = ["src/checkout.py", "tests/test_checkout.py"]

    github = AsyncMock()
    github.open_pull_request.return_value = PullRequestResult(
        number=7, url="https://github.com/acme/widgets/pull/7", branch="confluence-sync/x"
    )

    email_client = AsyncMock()
    store = PageStore(settings.page_store_path)

    change_engine = AsyncMock()
    change_engine.implement_change.return_value = ChangeAgentResult(
        success=True, summary="Added PayPal support."
    )

    judge = AsyncMock()
    judge.evaluate.return_value = JudgeResult(
        verdict="approved", reasoning="Diff matches the spec change.", concerns=[]
    )

    jira = AsyncMock()
    jira.create_issue.return_value = JiraIssueResult(
        key="SD-1", url="https://neurealm-team-juadifpx.atlassian.net/browse/SD-1"
    )

    return PipelineDeps(
        settings=settings,
        confluence=confluence,
        store=store,
        git=git,
        github=github,
        email_client=email_client,
        change_engine=change_engine,
        run_store=RunStore(settings.runs_store_path),
        judge=judge,
        jira=jira,
        pending_approvals=PendingApprovalStore(settings.pending_approvals_store_path),
    )


async def test_no_change_detected_skips_everything(settings):
    page = _page(1)
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, page.body_html, page.url))

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "no_change_detected"
    deps.git.clone.assert_not_called()
    deps.change_engine.implement_change.assert_not_called()
    deps.github.open_pull_request.assert_not_called()
    deps.email_client.send_email.assert_not_called()


async def test_force_reprocesses_a_page_with_no_detected_change(settings, monkeypatch):
    """The Retrigger button's backing case: same version/checksum as last
    time (e.g. only a label was added), but force=True means process it
    anyway instead of short-circuiting to no_change_detected."""
    page = _page(1)
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, page.body_html, page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps, force=True)

    assert result.status == "opened_pr"
    assert result.diff is not None
    assert result.diff.forced is True
    deps.change_engine.implement_change.assert_awaited_once()
    deps.github.open_pull_request.assert_awaited_once()


async def test_page_without_required_label_is_ignored_before_any_real_work(settings):
    settings.confluence_allowed_labels = "brd,spec-for-agent"
    page = _page(1, labels=["meeting-notes"])
    deps = _make_deps(settings, page=page)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "ignored"
    assert "brd" in (result.error or "")
    deps.git.clone.assert_not_called()
    deps.change_engine.implement_change.assert_not_called()
    deps.github.open_pull_request.assert_not_called()
    deps.email_client.send_email.assert_not_called()

    runs = deps.run_store.list_runs()
    assert runs[0]["status"] == "ignored"


async def test_page_label_match_is_case_insensitive(settings, monkeypatch):
    settings.confluence_allowed_labels = "BRD"
    page = _page(2, body="<p>spec v2</p>", labels=["brd"])
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"


async def test_no_allowed_labels_configured_means_no_filtering(settings):
    """Empty CONFLUENCE_ALLOWED_LABELS (the default) processes every page --
    this is the backward-compatible behavior for anyone who hasn't set up
    labels at all.
    """
    settings.confluence_allowed_labels = ""
    page = _page(1, labels=[])  # no labels on the page at all
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, page.body_html, page.url))

    result = await run_pipeline("123456", deps=deps)

    # falls through to the normal no-op path (same version, no labels involved)
    assert result.status == "no_change_detected"


async def test_successful_change_opens_pr_and_emails_team(settings, monkeypatch):
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.pull_request is not None
    assert result.pull_request.number == 7
    deps.change_engine.implement_change.assert_awaited_once()
    deps.git.clone.assert_awaited_once()
    deps.git.push.assert_awaited_once()
    deps.github.open_pull_request.assert_awaited_once()
    deps.email_client.send_email.assert_awaited_once()

    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["version"] == 2

    runs = deps.run_store.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "opened_pr"
    assert runs[0]["engine"] == settings.change_agent_engine
    assert runs[0]["pr_number"] == 7
    assert runs[0]["pr_url"] == "https://github.com/acme/widgets/pull/7"
    assert runs[0]["files_changed"] == ["src/checkout.py", "tests/test_checkout.py"]
    assert runs[0]["duration_seconds"] >= 0
    assert runs[0]["current_stage"] == "send_email"
    assert runs[0]["spec_diff"] is not None
    assert "spec v2" in runs[0]["spec_diff"]
    assert runs[0]["flagged_scope_gap"] is False  # default summary has no "Next steps" heading


async def test_flagged_scope_gap_true_when_agent_summary_has_next_steps_heading(settings, monkeypatch):
    """Structural detection, not free-text guessing -- see
    agent/prompts.py's out-of-scope section, which instructs the agent to
    use exactly this heading when it judges another repo is genuinely
    needed. RunRecord.flagged_scope_gap is what drives the "more action
    needed" badge in runs.html/run_detail.html.
    """
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.change_engine.implement_change.return_value = ChangeAgentResult(
        success=True,
        summary="Implemented the API-side check.\n\nNext steps: add the `repo-ui` label and retrigger.",
    )

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    runs = deps.run_store.list_runs()
    assert runs[0]["flagged_scope_gap"] is True


def _enable_jira(settings) -> None:
    settings.jira_enabled = True
    settings.jira_base_url = "https://neurealm-team-juadifpx.atlassian.net"
    settings.jira_project_key = "SD"
    settings.jira_issue_type = "Story"


_FAKE_STORY = JiraStoryContent(
    summary="Support PayPal at checkout",
    description="The spec now requires PayPal as a payment option.",
    acceptance_criteria=["Customer can select PayPal", "Order total matches"],
    complexity="M",
    complexity_reason="Touches the payment provider integration and its tests.",
)


def test_build_deps_rejects_an_unsupported_repo_provider(settings):
    settings.repo_provider = "azure_devops"
    with pytest.raises(ValueError, match="REPO_PROVIDER"):
        build_deps(settings)


def test_build_deps_accepts_github(settings):
    settings.repo_provider = "github"
    deps = build_deps(settings)
    assert deps.settings.repo_provider == "github"


async def test_jira_disabled_by_default_never_creates_an_issue(settings, monkeypatch):
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.jira_issue is None
    deps.jira.create_issue.assert_not_called()
    deps.jira.add_comment.assert_not_called()


async def test_jira_enabled_creates_a_story_and_comments_the_pr_link(settings, monkeypatch):
    _enable_jira(settings)
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    async def _fake_story(settings, diff):
        return _FAKE_STORY

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)
    monkeypatch.setattr(orchestrator, "generate_story_content", _fake_story)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.jira_issue is not None
    assert result.jira_issue.key == "SD-1"
    assert result.jira_reused is False
    deps.jira.create_issue.assert_awaited_once_with(
        project_key="SD",
        issue_type="Story",
        summary=_FAKE_STORY.summary,
        description=_FAKE_STORY.description,
        acceptance_criteria=_FAKE_STORY.acceptance_criteria,
    )
    # Two comments: the raw spec diff, posted right after creation (kept out
    # of the description itself -- see jira/story_writer.py), then the PR
    # link once the run finishes.
    assert deps.jira.add_comment.await_count == 2
    diff_call, pr_link_call = deps.jira.add_comment.await_args_list
    assert diff_call.args[0] == "SD-1"
    assert "Spec diff" in diff_call.args[1]
    assert pr_link_call.args[0] == "SD-1"
    assert "https://github.com/acme/widgets/pull/7" in pr_link_call.args[1]

    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["jira_issue_key"] == "SD-1"

    runs = deps.run_store.list_runs()
    assert runs[0]["jira_issue_key"] == "SD-1"
    assert runs[0]["jira_reused"] is False


async def test_approval_required_stops_after_story_creation_and_never_clones(settings, monkeypatch):
    """JIRA_APPROVAL_REQUIRED's whole point: the story exists, but nothing
    downstream (clone, agent, tests, PR) runs until a separate resumed call
    -- see pipeline/approval_poller.py, not exercised here.
    """
    _enable_jira(settings)
    settings.jira_approval_required = True
    settings.jira_approved_status_name = "Approved"
    page = _page(1, body="<p>spec v1</p>")
    deps = _make_deps(settings, page=page)

    async def _fake_story(settings, diff):
        return _FAKE_STORY

    monkeypatch.setattr(orchestrator, "generate_story_content", _fake_story)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "awaiting_approval"
    assert result.jira_issue is not None
    assert result.jira_issue.key == "SD-1"
    deps.change_engine.implement_change.assert_not_awaited()
    deps.git.clone.assert_not_awaited()
    deps.github.open_pull_request.assert_not_awaited()

    pending = deps.pending_approvals.get("123456")
    assert pending is not None
    assert pending["jira_issue_key"] == "SD-1"

    # The page-version store must NOT advance -- an approval-gated run has
    # no durable success artifact yet (see the "only advance on a fully
    # successful run" rule the rest of the pipeline already follows).
    assert deps.store.get("123456") is None


async def test_approval_required_off_by_default_matches_todays_behavior(settings, monkeypatch):
    """The opt-in default itself: an account that never touches
    JIRA_APPROVAL_REQUIRED sees zero behavior change -- same assertion style
    as test_jira_enabled_creates_a_story_and_comments_the_pr_link, just
    confirming the new field defaults to False.
    """
    assert settings.jira_approval_required is False
    _enable_jira(settings)
    page = _page(1, body="<p>spec v1</p>")
    deps = _make_deps(settings, page=page)

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    async def _fake_story(settings, diff):
        return _FAKE_STORY

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)
    monkeypatch.setattr(orchestrator, "generate_story_content", _fake_story)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert deps.pending_approvals.get("123456") is None


async def test_resume_after_approval_implements_the_saved_diff_without_refetching(settings, monkeypatch):
    """`resume` is what pipeline/approval_poller.py passes once a story
    reaches JIRA_APPROVED_STATUS_NAME -- confirms it skips straight to
    implementation using the exact saved diff/story, never re-creating a
    story or re-touching the label gate.
    """
    settings.jira_approval_required = True
    page = _page(1, body="<p>spec v1 (as approved)</p>")
    deps = _make_deps(settings, page=page)

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    pending: PendingApproval = {
        "page_id": "123456",
        "jira_issue_key": "SD-1",
        "jira_issue_url": "https://neurealm-team-juadifpx.atlassian.net/browse/SD-1",
        "previous_version": None,
        "diff_text": "(No prior version on record -- treating the full current body as the initial spec.)\n\nspec",
        "is_first_seen": True,
        "body_checksum": "irrelevant-for-this-test",
        "page_version": 1,
        "page_body_checksum": "irrelevant-for-this-test",
        "page_title": "Checkout Flow Spec",
        "page_url": page.url,
        "page_body_html": page.body_html,
        "page_labels": [],
        "run_id": "resumed-run-1",
        "created_at": "2026-01-01T00:00:00+00:00",
    }

    result = await run_pipeline("123456", deps=deps, resume=pending)

    assert result.status == "opened_pr"
    assert result.jira_issue is not None
    assert result.jira_issue.key == "SD-1"
    deps.confluence.fetch_page.assert_not_awaited()  # never re-fetched the live page
    deps.jira.create_issue.assert_not_awaited()  # never created a second story


async def test_jira_reuses_still_open_story_instead_of_creating_a_new_one(settings, monkeypatch):
    _enable_jira(settings)
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url, jira_issue_key="SD-9"))
    deps.jira.get_issue_status.return_value = JiraIssueStatus(
        key="SD-9", status_name="In Progress", status_category="indeterminate"
    )

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.jira_issue is not None
    assert result.jira_issue.key == "SD-9"
    assert result.jira_reused is True
    deps.jira.create_issue.assert_not_called()

    # Reusing a story now refreshes its description with the latest spec
    # state (instead of leaving it stuck with whatever it said when first
    # created) and posts the diff as a comment, same visibility a brand-new
    # story gets -- then a second comment for the PR link once the run
    # finishes (_sync_jira_on_finish).
    deps.jira.update_description.assert_awaited_once()
    assert deps.jira.update_description.await_args.args[0] == "SD-9"
    assert deps.jira.add_comment.await_count == 2
    diff_call, pr_link_call = deps.jira.add_comment.await_args_list
    assert diff_call.args[0] == "SD-9"
    assert "Spec diff" in diff_call.args[1]
    assert pr_link_call.args[0] == "SD-9"

    # Persisted even before the run's final success -- see
    # PageStore.remember_jira_issue.
    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["jira_issue_key"] == "SD-9"


async def test_jira_reuses_a_story_created_via_sprint_planning(settings, monkeypatch):
    """Regression test: a page whose story was created via Sprint Planning
    (SprintPlanStore) rather than through this pipeline's own reuse
    tracking (PageStore) has NO PageStore record at all. Retriggering that
    page (poll/webhook/manual retrigger, no `resume`) must still find and
    reuse that story instead of creating a duplicate -- confirmed live:
    retriggering page 5898241 (story KAN-33) created a second story, KAN-55.
    """
    _enable_jira(settings)
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    # No deps.store.put(...) here -- PageStore has never heard of this page.
    plan_store = SprintPlanStore(settings.sprint_plan_store_path)
    plan_store.put({
        "sprint_tag": "sprint-24",
        "gate_label": "",
        "space_key": "SD",
        "created_at": "2026-01-01T00:00:00+00:00",
        "pages": [{"page_id": "123456", "jira_issue_key": "KAN-33"}],
        "order": ["123456"],
    })
    deps.jira.get_issue_status.return_value = JiraIssueStatus(
        key="KAN-33", status_name="In Progress", status_category="indeterminate"
    )

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.jira_issue is not None
    assert result.jira_issue.key == "KAN-33"
    assert result.jira_reused is True
    deps.jira.create_issue.assert_not_called()


async def test_jira_story_visible_in_the_live_run_before_the_pipeline_finishes(settings, monkeypatch):
    """Regression test: RunStore.upsert_run REPLACES the whole record every
    call (see its own docstring) -- mark_stage() has to re-include every
    already-known field on each call, or it gets wiped. jira_issue_key/url/
    reused weren't included, so a still-running run's "Progress" view (run
    detail page) showed a blank Jira Story step for the story's whole
    lifetime -- confirmed live -- only filling in once the pipeline
    finished and finish() wrote the terminal record.
    """
    _enable_jira(settings)
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url, jira_issue_key="SD-9"))
    deps.jira.get_issue_status.return_value = JiraIssueStatus(
        key="SD-9", status_name="In Progress", status_category="indeterminate"
    )

    observed_mid_run = {}

    async def _clone_side_effect(*args, **kwargs):
        runs = deps.run_store.list_runs()
        if runs:
            observed_mid_run["jira_issue_key"] = runs[0]["jira_issue_key"]
            observed_mid_run["jira_reused"] = runs[0]["jira_reused"]

    deps.git.clone.side_effect = _clone_side_effect

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert observed_mid_run["jira_issue_key"] == "SD-9"
    assert observed_mid_run["jira_reused"] is True


async def test_jira_story_survives_a_follow_up_comment_failure(settings, monkeypatch):
    """Regression test for the actual duplicate-story bug: creating the
    issue succeeds, but the *follow-up* diff comment throws. Previously one
    shared try/except around the whole block reset jira_issue back to None
    when that happened, discarding the reference to the story that had just
    been created -- so the next run for this page had no memory of it and
    created a duplicate. Now each follow-up step is independently guarded.
    """
    _enable_jira(settings)
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.jira.add_comment.side_effect = RuntimeError("Jira comment API is down")

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    async def _fake_story(settings, diff):
        return _FAKE_STORY

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)
    monkeypatch.setattr(orchestrator, "generate_story_content", _fake_story)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    # The story itself must survive even though every comment attempt on it
    # failed -- this is the actual bug: it used to come back None here.
    assert result.jira_issue is not None
    assert result.jira_issue.key == "SD-1"

    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["jira_issue_key"] == "SD-1"

    runs = deps.run_store.list_runs()
    assert runs[0]["jira_issue_key"] == "SD-1"


async def test_jira_story_key_persisted_even_if_pipeline_fails_after_creation(settings, monkeypatch):
    """The other half of the duplicate-story bug: the story is created
    before any code is touched, but the change engine then fails (the real
    incident this was caught from -- a misconfigured engine CLI). Without
    remember_jira_issue persisting the key immediately, the next retry for
    this same unresolved page wouldn't know a story already exists and
    would create a second one.
    """
    _enable_jira(settings)
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.change_engine.implement_change.return_value = ChangeAgentResult(
        success=False, summary="", raw_log="engine CLI not found on PATH"
    )

    async def _fake_story(settings, diff):
        return _FAKE_STORY

    monkeypatch.setattr(orchestrator, "generate_story_content", _fake_story)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "error"
    assert result.jira_issue is not None
    assert result.jira_issue.key == "SD-1"

    # Persisted despite the run overall failing -- the full put() further
    # down (which would have recorded this too) is never reached on this
    # path, so remember_jira_issue is the only thing that saves it.
    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["jira_issue_key"] == "SD-1"
    # And nothing else about the stored page moved -- still v1, so the next
    # run's compute_diff() still correctly treats this page as unresolved
    # and keeps retrying rather than silently giving up.
    assert stored["version"] == 1


async def test_jira_creation_failure_fails_open_and_still_opens_pr(settings, monkeypatch):
    _enable_jira(settings)
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.jira.create_issue.side_effect = RuntimeError("Jira is down")

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    async def _fake_story(settings, diff):
        return _FAKE_STORY

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)
    monkeypatch.setattr(orchestrator, "generate_story_content", _fake_story)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.jira_issue is None
    deps.github.open_pull_request.assert_awaited_once()


async def test_a_running_placeholder_is_visible_before_the_pipeline_finishes(settings, monkeypatch):
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    observed_mid_run = {}

    async def _clone_side_effect(*args, **kwargs):
        runs = deps.run_store.list_runs()
        if runs:
            observed_mid_run["status"] = runs[0]["status"]
            observed_mid_run["current_stage"] = runs[0]["current_stage"]

    deps.git.clone.side_effect = _clone_side_effect

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert observed_mid_run["status"] == "running"
    assert observed_mid_run["current_stage"] == "clone_repo"
    # ... and the placeholder is replaced, not left behind as a second row
    assert result.status == "opened_pr"
    runs = deps.run_store.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "opened_pr"


async def test_failing_tests_blocks_pr_and_does_not_advance_store(settings, monkeypatch):
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=False, output="1 failed: ModuleNotFoundError: no module named 'django'", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "tests_failed"
    deps.github.open_pull_request.assert_not_called()
    deps.email_client.send_email.assert_not_called()

    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["version"] == 1

    runs = deps.run_store.list_runs()
    assert "ModuleNotFoundError" in runs[0]["test_output"]
    assert runs[0]["current_stage"] == "run_tests"


async def test_judge_rejection_opens_a_flagged_draft_pr_and_still_advances_store(settings, monkeypatch):
    """The judge is advisory, not a gate: a rejection must not silently
    discard the change -- it opens a draft PR titled to flag it for human
    attention, with the rubric explaining why, and the page store still
    advances (a durable PR now exists for this diff, so retrying it on the
    next webhook would just open a second, duplicate PR).
    """
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.github.open_pull_request.return_value = PullRequestResult(
        number=7, url="https://github.com/acme/widgets/pull/7", branch="confluence-sync/x", draft=True
    )
    deps.judge.evaluate.return_value = JudgeResult(
        verdict="rejected",
        reasoning="The diff only stubs out the PayPal button; no actual checkout logic was added.",
        concerns=["No server-side handling of the PayPal callback."],
        criteria=[
            JudgeCriterion(
                key="implements_spec", label="Implements the spec change", assessment="fail",
                note="Only a stub button was added; no checkout logic.",
            ),
            JudgeCriterion(
                key="scoped", label="Scoped to the change", assessment="pass", note="No unrelated files touched.",
            ),
            JudgeCriterion(
                key="tests_cover_behavior", label="Tests cover the new behavior", assessment="fail",
                note="No new tests were added.",
            ),
        ],
    )

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "judge_rejected"
    deps.github.open_pull_request.assert_awaited_once()
    call_kwargs = deps.github.open_pull_request.await_args.kwargs
    assert call_kwargs["draft"] is True
    assert call_kwargs["title"].startswith("[Needs Work] ")
    assert "Needs review before merge" in call_kwargs["body"]
    assert "PayPal callback" in call_kwargs["body"]
    assert "Implements the spec change | ❌ fail" in call_kwargs["body"]
    deps.email_client.send_email.assert_not_called()

    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["version"] == 2  # advanced -- a PR now exists for this diff

    runs = deps.run_store.list_runs()
    # Reaches (and completes) the send_email stage even though no email
    # actually gets sent for a judge_rejected repo -- the pipeline still
    # has to progress through that stage so a *mixed* multi-repo run (some
    # repos opened cleanly, others judge_rejected) still emails the ones
    # that qualify. See pipeline/orchestrator.py's email_targets filter.
    assert runs[0]["current_stage"] == "send_email"
    assert runs[0]["judge_verdict"] == "rejected"
    assert runs[0]["pr_draft"] is True
    assert runs[0]["pr_number"] == 7
    assert "PayPal callback" in runs[0]["judge_concerns"][0]
    assert len(runs[0]["judge_criteria"]) == 3


async def test_judge_skipped_without_anthropic_key_still_opens_pr(settings, monkeypatch):
    settings.anthropic_api_key = ""
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    deps.judge.evaluate.assert_not_called()

    runs = deps.run_store.list_runs()
    assert runs[0]["judge_verdict"] == "skipped"


async def test_judge_disabled_is_never_invoked(settings, monkeypatch):
    settings.judge_enabled = False
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    deps.judge.evaluate.assert_not_called()

    runs = deps.run_store.list_runs()
    assert runs[0]["judge_verdict"] is None


async def test_judge_call_failure_fails_open_and_still_opens_pr(settings, monkeypatch):
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.judge.evaluate.side_effect = RuntimeError("connection reset")

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.pull_request is not None

    runs = deps.run_store.list_runs()
    assert runs[0]["judge_verdict"] == "skipped"
    assert "connection reset" in runs[0]["judge_reasoning"]


async def test_approved_with_warnings_opens_a_normal_non_draft_pr(settings, monkeypatch):
    """approved_with_warnings gates identically to a clean approval -- only
    a "rejected" verdict changes the kind of PR that opens.
    """
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.judge.evaluate.return_value = JudgeResult(
        verdict="approved_with_warnings",
        reasoning="Implements the spec; test coverage for the new path is thin.",
        concerns=["Only the happy path is tested."],
        criteria=[
            JudgeCriterion(
                key="implements_spec", label="Implements the spec change", assessment="pass", note="Matches spec.",
            ),
            JudgeCriterion(key="scoped", label="Scoped to the change", assessment="pass", note="Scoped."),
            JudgeCriterion(
                key="tests_cover_behavior", label="Tests cover the new behavior", assessment="warning",
                note="Only the happy path is covered.",
            ),
        ],
    )

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    call_kwargs = deps.github.open_pull_request.await_args.kwargs
    assert call_kwargs["draft"] is False
    assert not call_kwargs["title"].startswith("[Needs Work]")
    assert "Tests cover the new behavior | ⚠️ warning" in call_kwargs["body"]
    deps.email_client.send_email.assert_awaited_once()  # unlike a rejection, email still goes out

    runs = deps.run_store.list_runs()
    assert runs[0]["judge_verdict"] == "approved_with_warnings"
    assert runs[0]["pr_draft"] is False


async def test_tests_fail_then_pass_on_retry_records_two_attempts(settings, monkeypatch):
    call_count = {"n": 0}

    async def _fake_run_tests(repo_dir, command):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return RepoTestResult(passed=False, output="AssertionError: expected PayPal button", command=command)
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.attempts == 2
    assert deps.change_engine.implement_change.await_count == 2
    # The second attempt must be told why the first one failed.
    second_call_kwargs = deps.change_engine.implement_change.await_args_list[1].kwargs
    assert "expected PayPal button" in second_call_kwargs["retry_context"]

    runs = deps.run_store.list_runs()
    assert runs[0]["attempts"] == 2


async def test_lint_failure_feeds_back_into_the_same_retry_loop_as_a_test_failure(settings, monkeypatch):
    """RepoTarget.lint_command (Phase 4) reuses run_tests's own pass/fail
    shape and the existing retry loop -- a lint failure on attempt 1 must
    retry exactly like a test failure would, and only run at all once tests
    themselves already passed.
    """
    settings.target_repos_json = json.dumps(
        [{"target_repo": "acme/widgets", "base_branch": "main", "test_command": "pytest", "lint_command": "ruff check ."}]
    )
    page = _page(1, body="<p>spec v1</p>")
    deps = _make_deps(settings, page=page)

    call_log: list[str] = []

    async def _fake_run_tests(repo_dir, command):
        call_log.append(command)
        if command == "ruff check ." and call_log.count("ruff check .") == 1:
            return RepoTestResult(passed=False, output="E501 line too long", command=command)
        return RepoTestResult(passed=True, output="ok", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.attempts == 2
    # pytest ran on both attempts; lint only ran once tests passed each time.
    assert call_log == ["pytest", "ruff check .", "pytest", "ruff check ."]
    second_call_kwargs = deps.change_engine.implement_change.await_args_list[1].kwargs
    assert "E501 line too long" in second_call_kwargs["retry_context"]


async def test_invalid_lint_command_fails_open_instead_of_blocking_the_pr(settings, monkeypatch):
    """A lint_command that can't even be run (typo, missing binary --
    crashed=True, see testing/test_runner.py) must NOT block an otherwise
    passing, tested change -- lint is optional, unlike the test command
    itself. Distinct from test_lint_failure_feeds_back_into_the_same_retry_
    loop_as_a_test_failure above, where the lint command DID run and found
    a real violation, which correctly still blocks/retries.
    """
    settings.target_repos_json = json.dumps(
        [{"target_repo": "acme/widgets", "base_branch": "main", "test_command": "pytest", "lint_command": "this-binary-does-not-exist"}]
    )
    page = _page(1, body="<p>spec v1</p>")
    deps = _make_deps(settings, page=page)

    async def _fake_run_tests(repo_dir, command):
        if command == "this-binary-does-not-exist":
            return RepoTestResult(
                passed=False, crashed=True, output="Could not run this command: [Errno 2] No such file", command=command
            )
        return RepoTestResult(passed=True, output="ok", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.attempts == 1  # never retried -- the crash isn't treated as a real failure


async def test_lint_command_blank_by_default_never_runs(settings, monkeypatch):
    async def _fake_run_tests(repo_dir, command):
        assert command == "pytest"  # never called with anything else
        return RepoTestResult(passed=True, output="ok", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)
    page = _page(1, body="<p>spec v1</p>")
    deps = _make_deps(settings, page=page)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"


async def test_tests_still_failing_after_max_attempts_records_tests_failed(settings, monkeypatch):
    settings.change_agent_max_attempts = 2

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=False, output="still failing", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "tests_failed"
    assert result.attempts == 2
    assert deps.change_engine.implement_change.await_count == 2
    deps.github.open_pull_request.assert_not_called()

    stored = deps.store.get("123456")
    assert stored["version"] == 1  # no PR ever opened -- store must not advance

    runs = deps.run_store.list_runs()
    assert runs[0]["attempts"] == 2


async def test_change_agent_max_attempts_one_disables_retrying(settings, monkeypatch):
    settings.change_agent_max_attempts = 1

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=False, output="failing", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "tests_failed"
    assert result.attempts == 1
    deps.change_engine.implement_change.assert_awaited_once()


async def test_rejection_syncs_needs_work_label_and_clears_warning_label(settings, monkeypatch):
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.judge.evaluate.return_value = JudgeResult(verdict="rejected", reasoning="Nope.", concerns=["Missing logic."])

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "judge_rejected"
    deps.github.ensure_label.assert_awaited_once()
    assert deps.github.ensure_label.await_args.args[1] == orchestrator.LABEL_NEEDS_WORK
    deps.github.add_labels.assert_awaited_once_with("acme/widgets", 7, [orchestrator.LABEL_NEEDS_WORK])
    deps.github.remove_label.assert_any_await("acme/widgets", 7, orchestrator.LABEL_WARNING)


async def test_clean_approval_removes_both_verdict_labels(settings, monkeypatch):
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    deps.github.ensure_label.assert_not_called()
    deps.github.add_labels.assert_not_called()
    deps.github.remove_label.assert_any_await("acme/widgets", 7, orchestrator.LABEL_NEEDS_WORK)
    deps.github.remove_label.assert_any_await("acme/widgets", 7, orchestrator.LABEL_WARNING)


async def test_reuses_still_open_pr_branch_instead_of_opening_a_new_one(settings, monkeypatch):
    page = _page(3, body="<p>spec v3</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(
        _stored(2, "<p>spec v2</p>", page.url, open_pr_number=42, open_pr_branch="confluence-sync/page-123456-v2-1")
    )
    deps.github.get_pull_request.return_value = PullRequestStatus(number=42, state="open", merged=False)
    deps.github.update_pull_request.return_value = PullRequestResult(
        number=42, url="https://github.com/acme/widgets/pull/42", branch="confluence-sync/page-123456-v2-1"
    )

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.reused_pr is True
    deps.github.get_pull_request.assert_awaited_once_with("acme/widgets", 42)
    deps.git.checkout_existing_branch.assert_awaited_once()
    assert deps.git.checkout_existing_branch.await_args.args[1] == "confluence-sync/page-123456-v2-1"
    deps.git.create_branch.assert_not_called()
    deps.github.update_pull_request.assert_awaited_once()
    deps.github.open_pull_request.assert_not_called()

    runs = deps.run_store.list_runs()
    assert runs[0]["reused_pr"] is True
    assert runs[0]["pr_number"] == 42

    stored = deps.store.get("123456")
    assert stored["open_pr_number"] == 42
    assert stored["open_pr_branch"] == "confluence-sync/page-123456-v2-1"


async def test_does_not_reuse_a_merged_pr_opens_new_one_instead(settings, monkeypatch):
    page = _page(3, body="<p>spec v3</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(
        _stored(2, "<p>spec v2</p>", page.url, open_pr_number=42, open_pr_branch="confluence-sync/page-123456-v2-1")
    )
    deps.github.get_pull_request.return_value = PullRequestStatus(number=42, state="closed", merged=True)

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.reused_pr is False
    deps.git.create_branch.assert_awaited_once()
    deps.git.checkout_existing_branch.assert_not_called()
    deps.github.open_pull_request.assert_awaited_once()
    deps.github.update_pull_request.assert_not_called()


async def test_pr_status_lookup_failure_falls_back_to_opening_a_new_pr(settings, monkeypatch):
    page = _page(3, body="<p>spec v3</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(
        _stored(2, "<p>spec v2</p>", page.url, open_pr_number=42, open_pr_branch="confluence-sync/page-123456-v2-1")
    )
    deps.github.get_pull_request.side_effect = RuntimeError("GitHub API down")

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.reused_pr is False
    deps.github.open_pull_request.assert_awaited_once()


async def test_branch_checkout_failure_falls_back_to_a_new_branch_and_pr(settings, monkeypatch):
    page = _page(3, body="<p>spec v3</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(
        _stored(2, "<p>spec v2</p>", page.url, open_pr_number=42, open_pr_branch="confluence-sync/page-123456-v2-1")
    )
    deps.github.get_pull_request.return_value = PullRequestStatus(number=42, state="open", merged=False)
    deps.git.checkout_existing_branch.side_effect = RuntimeError("branch gone")

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.reused_pr is False
    deps.git.create_branch.assert_awaited_once()
    deps.github.open_pull_request.assert_awaited_once()


async def test_push_failure_on_reused_branch_reports_error_without_opening_a_duplicate_pr(settings, monkeypatch):
    page = _page(3, body="<p>spec v3</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(
        _stored(2, "<p>spec v2</p>", page.url, open_pr_number=42, open_pr_branch="confluence-sync/page-123456-v2-1")
    )
    deps.github.get_pull_request.return_value = PullRequestStatus(number=42, state="open", merged=False)
    deps.git.push.side_effect = RuntimeError("non-fast-forward")

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "error"
    assert "42" in result.error
    deps.github.open_pull_request.assert_not_called()
    deps.github.update_pull_request.assert_not_called()

    # Not advanced -- no durable artifact was actually produced this run.
    stored = deps.store.get("123456")
    assert stored["version"] == 2


def test_build_email_client_defaults_to_sendgrid(settings):
    from confluence_pr_agent.notifications.sendgrid_client import SendGridClient
    from confluence_pr_agent.pipeline.orchestrator import _build_email_client

    settings.email_provider = "sendgrid"
    assert isinstance(_build_email_client(settings), SendGridClient)


def test_build_email_client_selects_postmark(settings):
    from confluence_pr_agent.notifications.postmark_client import PostmarkClient
    from confluence_pr_agent.pipeline.orchestrator import _build_email_client

    settings.email_provider = "postmark"
    assert isinstance(_build_email_client(settings), PostmarkClient)


async def test_change_engine_failure_does_not_open_pr(settings):
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.change_engine.implement_change.return_value = ChangeAgentResult(
        success=False, summary="Ran out of turns."
    )

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "error"
    deps.github.open_pull_request.assert_not_called()
    deps.email_client.send_email.assert_not_called()

    runs = deps.run_store.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "error"
    assert runs[0]["current_stage"] == "ai_agent"


async def test_no_change_detected_is_still_recorded_as_a_run(settings):
    page = _page(1)
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, page.body_html, page.url))

    await run_pipeline("123456", deps=deps)

    runs = deps.run_store.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "no_change_detected"
    assert runs[0]["spec_diff"] is None  # nothing to show -- diff_text is empty for an unchanged page


async def test_missing_sendgrid_key_skips_email_without_failing_the_run(settings, monkeypatch):
    settings.sendgrid_api_key = ""
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.pull_request is not None
    assert result.email_sent is False
    assert "No API key configured" in (result.email_error or "")
    deps.email_client.send_email.assert_not_called()

    runs = deps.run_store.list_runs()
    assert runs[0]["status"] == "opened_pr"
    assert runs[0]["pr_number"] == 7
    assert runs[0]["email_sent"] is False


async def test_email_send_failure_does_not_erase_the_successful_pr(settings, monkeypatch):
    """Regression test: a blank/invalid SendGrid key used to raise an
    unhandled exception from send_email *after* the PR was already opened,
    which the outer except-block caught and reported as a total failure --
    discarding the pull_request from the recorded result even though a real
    PR existed on GitHub. Confirmed live against care-scheduler PR #1.
    """
    page = _page(2, body="<p>spec v2</p>")
    deps = _make_deps(settings, page=page)
    deps.store.put(_stored(1, "<p>spec v1</p>", page.url))
    deps.email_client.send_email.side_effect = RuntimeError("Illegal header value b'Bearer '")

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert result.pull_request is not None
    assert result.pull_request.url == "https://github.com/acme/widgets/pull/7"
    assert result.email_sent is False
    assert "Bearer" in (result.email_error or "")

    runs = deps.run_store.list_runs()
    assert runs[0]["status"] == "opened_pr"
    assert runs[0]["pr_url"] == "https://github.com/acme/widgets/pull/7"
    assert runs[0]["email_error"] is not None

    # And the page store still advances -- the actual code change/PR did
    # succeed, so this should not be retried on the next webhook delivery.
    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["version"] == 2


# --- Multi-repo (TARGET_REPOS_JSON) ------------------------------------


def _two_repo_config() -> str:
    return json.dumps(
        [
            {"target_repo": "acme/backend", "base_branch": "main", "test_command": "pytest", "label": "repo:backend"},
            {"target_repo": "acme/agents", "base_branch": "main", "test_command": "pytest", "label": "repo:agents"},
        ]
    )


async def test_no_repo_matched_when_page_labels_dont_match_any_configured_repo(settings, monkeypatch):
    settings.target_repos_json = _two_repo_config()
    page = _page(2, body="<p>spec v2</p>", labels=["some-other-label"])
    deps = _make_deps(settings, page=page)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "no_repo_matched"
    deps.git.clone.assert_not_called()
    deps.change_engine.implement_change.assert_not_called()


async def test_multi_repo_routes_by_label_only_touches_the_matching_repo(settings, monkeypatch):
    settings.target_repos_json = _two_repo_config()
    page = _page(2, body="<p>spec v2</p>", labels=["repo:backend"])
    deps = _make_deps(settings, page=page)

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert len(result.repo_results) == 1
    assert result.repo_results[0].target_repo == "acme/backend"
    deps.git.clone.assert_awaited_once()
    assert deps.git.clone.await_args.args[0] == "acme/backend"
    deps.github.open_pull_request.assert_awaited_once()
    assert deps.github.open_pull_request.await_args.kwargs["owner_repo"] == "acme/backend"


async def test_multi_repo_both_matched_repos_get_their_own_pr(settings, monkeypatch):
    settings.target_repos_json = _two_repo_config()
    page = _page(2, body="<p>spec v2</p>", labels=["repo:backend", "repo:agents"])
    deps = _make_deps(settings, page=page)

    pr_counter = iter([10, 11])

    async def _open_pr_side_effect(owner_repo, head_branch, base_branch, title, body, draft=False):
        number = next(pr_counter)
        return PullRequestResult(number=number, url=f"https://github.com/{owner_repo}/pull/{number}", branch=head_branch)

    deps.github.open_pull_request.side_effect = _open_pr_side_effect

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "opened_pr"
    assert deps.git.clone.await_count == 2
    cloned_repos = {call.args[0] for call in deps.git.clone.await_args_list}
    assert cloned_repos == {"acme/backend", "acme/agents"}

    statuses = {r.target_repo: r.status for r in result.repo_results}
    assert statuses == {"acme/backend": "opened_pr", "acme/agents": "opened_pr"}

    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["repo_prs"]["acme/backend"]["open_pr_number"] in (10, 11)
    assert stored["repo_prs"]["acme/agents"]["open_pr_number"] in (10, 11)


async def test_multi_repo_one_repo_failing_does_not_block_the_other(settings, monkeypatch):
    """Partial failure: one repo's PR opens cleanly, the other's GitHub call
    fails -- the overall run reflects the worst outcome (error), but the
    successful repo's PR isn't lost, and its reuse info is still persisted.
    """
    settings.target_repos_json = _two_repo_config()
    page = _page(2, body="<p>spec v2</p>", labels=["repo:backend", "repo:agents"])
    deps = _make_deps(settings, page=page)

    async def _open_pr_side_effect(owner_repo, head_branch, base_branch, title, body, draft=False):
        if owner_repo == "acme/backend":
            return PullRequestResult(number=10, url="https://github.com/acme/backend/pull/10", branch=head_branch)
        raise RuntimeError("GitHub API is down")

    deps.github.open_pull_request.side_effect = _open_pr_side_effect

    async def _fake_run_tests(repo_dir, command):
        return RepoTestResult(passed=True, output="2 passed", command=command)

    monkeypatch.setattr(orchestrator, "run_tests", _fake_run_tests)

    result = await run_pipeline("123456", deps=deps)

    assert result.status == "error"  # worst-of aggregate across repos
    statuses = {r.target_repo: r.status for r in result.repo_results}
    assert statuses == {"acme/backend": "opened_pr", "acme/agents": "error"}
    failed = next(r for r in result.repo_results if r.target_repo == "acme/agents")
    assert "GitHub API is down" in failed.error
    # Backward-compat single-error summary string still mentions the failure.
    assert "acme/agents" in result.error

    # The successful repo's PR is still tracked for reuse next time, even
    # though the overall run is reported as an error.
    stored = deps.store.get("123456")
    assert stored is not None
    assert stored["repo_prs"]["acme/backend"]["open_pr_number"] == 10
    assert "acme/agents" not in stored["repo_prs"]
