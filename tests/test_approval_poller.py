from __future__ import annotations

from unittest.mock import AsyncMock

from confluence_pr_agent.confluence.diff import _to_plain_text, compute_checksum
from confluence_pr_agent.models import JiraIssueStatus, PageSnapshot
from confluence_pr_agent.pipeline import approval_poller
from confluence_pr_agent.storage.pending_approval_store import PendingApproval, PendingApprovalStore


def _pending(body_html: str) -> PendingApproval:
    return {
        "page_id": "123456",
        "jira_issue_key": "SD-1",
        "jira_issue_url": "https://example.atlassian.net/browse/SD-1",
        "previous_version": None,
        "diff_text": "(No prior version on record...)\n\nspec",
        "is_first_seen": True,
        "body_checksum": compute_checksum(_to_plain_text(body_html)),
        "page_version": 1,
        "page_body_checksum": compute_checksum(_to_plain_text(body_html)),
        "page_title": "Checkout Flow Spec",
        "page_url": "https://example.atlassian.net/wiki/spaces/SD/pages/123456",
        "page_body_html": body_html,
        "page_labels": [],
        "run_id": "run-1",
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def _fake_deps(settings, *, page_html: str, status_name: str):
    deps = AsyncMock()
    deps.confluence.fetch_page.return_value = PageSnapshot(
        page_id="123456",
        title="Checkout Flow Spec",
        version=1,
        body_html=page_html,
        url="https://example.atlassian.net/wiki/spaces/SD/pages/123456",
        labels=[],
    )
    deps.jira.get_issue_status.return_value = JiraIssueStatus(
        key="SD-1", status_name=status_name, status_category="indeterminate"
    )
    # Real store, not a mock -- its methods are sync (see
    # storage/pending_approval_store.py), unlike everything else on deps.
    deps.pending_approvals = PendingApprovalStore(settings.pending_approvals_store_path)
    return deps


async def test_check_one_resumes_when_status_matches_approved_name(settings, monkeypatch):
    settings.jira_approval_required = True
    settings.jira_approved_status_name = "Approved"
    body = "<p>spec v1</p>"
    entry = _pending(body)
    deps = _fake_deps(settings, page_html=body, status_name="Approved")

    monkeypatch.setattr(approval_poller, "build_deps", lambda s: deps)
    resume_calls = []

    async def _fake_run_pipeline(page_id, deps=None, resume=None):
        resume_calls.append((page_id, resume))

    monkeypatch.setattr(approval_poller, "run_pipeline", _fake_run_pipeline)

    await approval_poller._check_one(settings, entry)

    assert len(resume_calls) == 1
    assert resume_calls[0][0] == "123456"
    assert resume_calls[0][1] == entry
    deps.jira.add_comment.assert_not_awaited()


async def test_check_one_does_nothing_when_status_not_yet_approved(settings, monkeypatch):
    settings.jira_approval_required = True
    settings.jira_approved_status_name = "Approved"
    body = "<p>spec v1</p>"
    entry = _pending(body)
    deps = _fake_deps(settings, page_html=body, status_name="In Review")

    monkeypatch.setattr(approval_poller, "build_deps", lambda s: deps)
    resume_calls = []

    async def _fake_run_pipeline(page_id, deps=None, resume=None):
        resume_calls.append(page_id)

    monkeypatch.setattr(approval_poller, "run_pipeline", _fake_run_pipeline)

    await approval_poller._check_one(settings, entry)

    assert resume_calls == []


async def test_check_one_flags_stale_when_page_body_changed_while_pending(settings, monkeypatch):
    settings.jira_approval_required = True
    settings.jira_approved_status_name = "Approved"
    entry = _pending("<p>spec v1</p>")
    # The live page now says something different than what was approved-pending.
    deps = _fake_deps(settings, page_html="<p>spec v1, now with an extra requirement</p>", status_name="Approved")

    monkeypatch.setattr(approval_poller, "build_deps", lambda s: deps)
    resume_calls = []

    async def _fake_run_pipeline(page_id, deps=None, resume=None):
        resume_calls.append(page_id)

    monkeypatch.setattr(approval_poller, "run_pipeline", _fake_run_pipeline)

    await approval_poller._check_one(settings, entry)

    assert resume_calls == []  # never resumes on stale content
    deps.jira.add_comment.assert_awaited_once()
    assert "changed again" in deps.jira.add_comment.await_args.args[1]

    updated = deps.pending_approvals.get("123456")
    assert updated is not None
    assert updated["stale_reason"]


async def test_check_pending_approvals_skips_when_gate_is_off(settings, monkeypatch):
    settings.jira_approval_required = False
    calls = []

    async def _fake_check_one(settings, entry):
        calls.append(entry)

    monkeypatch.setattr(approval_poller, "_check_one", _fake_check_one)
    await approval_poller.check_pending_approvals(settings)

    assert calls == []
