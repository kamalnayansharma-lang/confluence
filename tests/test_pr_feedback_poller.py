from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from confluence_pr_agent.confluence.diff import _to_plain_text, compute_checksum
from confluence_pr_agent.models import ChangeAgentResult, PullRequestStatus, RepoTestResult
from confluence_pr_agent.pipeline import pr_feedback_poller
from confluence_pr_agent.pipeline.orchestrator import LABEL_AGENT_OPENED
from confluence_pr_agent.storage.page_store import PageStore, StoredPage


def _stored_page(
    body_html: str = "<p>Spec content</p>",
    open_pr_number: int = 42,
    open_pr_branch: str = "confluence-sync/page-123-v1",
    last_seen_comment_id: int = 0,
    feedback_attempts: int = 0,
) -> StoredPage:
    return {
        "page_id": "123",
        "title": "Test Page",
        "version": 1,
        "body_html": body_html,
        "body_checksum": compute_checksum(_to_plain_text(body_html)),
        "url": "https://example.atlassian.net/wiki/spaces/SD/pages/123",
        "repo_prs": {
            "your-org/your-repo": {
                "open_pr_number": open_pr_number,
                "open_pr_branch": open_pr_branch,
                "last_seen_comment_id": last_seen_comment_id,
                "feedback_attempts": feedback_attempts,
            }
        },
    }


def _fake_deps(settings, store: PageStore):
    deps = AsyncMock()
    deps.store = store
    deps.github.test_connection.return_value = "bot-user"
    return deps


@pytest.mark.asyncio
async def test_pr_feedback_skips_when_disabled(settings):
    settings.pr_feedback_poll_enabled = False
    with patch("confluence_pr_agent.pipeline.pr_feedback_poller.build_deps") as mock_build_deps:
        await pr_feedback_poller.check_pr_feedback(settings)
        mock_build_deps.assert_not_called()


@pytest.mark.asyncio
async def test_pr_feedback_skips_closed_pr(settings, tmp_path):
    settings.pr_feedback_poll_enabled = True
    store = PageStore(tmp_path / "page_store.json")
    store.put(_stored_page())

    deps = _fake_deps(settings, store)
    deps.github.get_pull_request.return_value = PullRequestStatus(
        number=42, state="closed", merged=False, labels=[LABEL_AGENT_OPENED]
    )

    with patch("confluence_pr_agent.pipeline.pr_feedback_poller.build_deps", return_value=deps):
        await pr_feedback_poller.check_pr_feedback(settings)

    deps.github.list_review_comments.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_feedback_skips_pr_without_agent_opened_label(settings, tmp_path):
    settings.pr_feedback_poll_enabled = True
    store = PageStore(tmp_path / "page_store.json")
    store.put(_stored_page())

    deps = _fake_deps(settings, store)
    deps.github.get_pull_request.return_value = PullRequestStatus(
        number=42, state="open", merged=False, labels=["some-other-label"]
    )

    with patch("confluence_pr_agent.pipeline.pr_feedback_poller.build_deps", return_value=deps):
        await pr_feedback_poller.check_pr_feedback(settings)

    deps.github.list_review_comments.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_feedback_skips_bot_comments_and_old_comments(settings, tmp_path):
    settings.pr_feedback_poll_enabled = True
    store = PageStore(tmp_path / "page_store.json")
    store.put(_stored_page(last_seen_comment_id=100))

    deps = _fake_deps(settings, store)
    deps.github.get_pull_request.return_value = PullRequestStatus(
        number=42, state="open", merged=False, labels=[LABEL_AGENT_OPENED]
    )
    deps.github.list_review_comments.return_value = [
        {"id": 99, "user": {"login": "human"}, "body": "old comment"},
        {"id": 100, "user": {"login": "human"}, "body": "last seen comment"},
        {"id": 101, "user": {"login": "bot-user"}, "body": "bot comment"},
        {"id": 102, "user": {"login": "another-bot"}, "body": "🤖 **confluence-pr-agent** — fix attempt 1 of 3"},
    ]

    with patch("confluence_pr_agent.pipeline.pr_feedback_poller.build_deps", return_value=deps):
        await pr_feedback_poller.check_pr_feedback(settings)

    deps.git.clone.assert_not_awaited()
    deps.github.add_pull_request_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_feedback_success_flow(settings, tmp_path):
    settings.pr_feedback_poll_enabled = True
    settings.change_agent_max_attempts = 3
    store = PageStore(tmp_path / "page_store.json")
    store.put(_stored_page(last_seen_comment_id=50, feedback_attempts=1))

    deps = _fake_deps(settings, store)
    deps.github.get_pull_request.return_value = PullRequestStatus(
        number=42, state="open", merged=False, labels=[LABEL_AGENT_OPENED]
    )
    deps.github.list_review_comments.return_value = [
        {"id": 51, "user": {"login": "reviewer"}, "body": "Please fix the return type"},
    ]
    deps.change_engine.implement_change.return_value = ChangeAgentResult(
        success=True, summary="Fixed return type", files_changed=["src/foo.py"]
    )
    deps.git.head_sha.return_value = "a1b2c3d4e5f6"
    deps.git.changed_files.return_value = ["src/foo.py"]

    with patch("confluence_pr_agent.pipeline.pr_feedback_poller.build_deps", return_value=deps), \
         patch("confluence_pr_agent.pipeline.pr_feedback_poller.run_tests", return_value=RepoTestResult(passed=True, output="1 passed", command="pytest")):
        await pr_feedback_poller.check_pr_feedback(settings)

    deps.git.clone.assert_awaited_once()
    deps.git.checkout_existing_branch.assert_awaited_once()
    deps.git.commit_all.assert_awaited_once()
    deps.git.push.assert_awaited_once()

    # Verified reply signature and commit SHA
    deps.github.add_pull_request_comment.assert_awaited_once()
    call_args = deps.github.add_pull_request_comment.await_args
    assert call_args[0][0] == "your-org/your-repo"
    assert call_args[0][1] == 42
    reply_body = call_args[0][2]
    assert "fix attempt 2 of 3" in reply_body
    assert "a1b2c3d4e5f6" in reply_body
    assert "`src/foo.py`" in reply_body

    # Verify store updated
    updated = store.get("123")
    repo_entry = updated["repo_prs"]["your-org/your-repo"]
    assert repo_entry["last_seen_comment_id"] == 51
    assert repo_entry["feedback_attempts"] == 2


@pytest.mark.asyncio
async def test_pr_feedback_test_failure_flow(settings, tmp_path):
    settings.pr_feedback_poll_enabled = True
    settings.change_agent_max_attempts = 3
    store = PageStore(tmp_path / "page_store.json")
    store.put(_stored_page(last_seen_comment_id=50, feedback_attempts=0))

    deps = _fake_deps(settings, store)
    deps.github.get_pull_request.return_value = PullRequestStatus(
        number=42, state="open", merged=False, labels=[LABEL_AGENT_OPENED]
    )
    deps.github.list_review_comments.return_value = [
        {"id": 55, "user": {"login": "reviewer"}, "body": "Add extra validation"},
    ]
    deps.change_engine.implement_change.return_value = ChangeAgentResult(
        success=True, summary="Added validation", files_changed=["src/foo.py"]
    )

    with patch("confluence_pr_agent.pipeline.pr_feedback_poller.build_deps", return_value=deps), \
         patch("confluence_pr_agent.pipeline.pr_feedback_poller.run_tests", return_value=RepoTestResult(passed=False, output="FAILED test_foo.py", command="pytest")):
        await pr_feedback_poller.check_pr_feedback(settings)

    # Must NOT push on failed tests
    deps.git.commit_all.assert_not_awaited()
    deps.git.push.assert_not_awaited()

    # Verified reply explains failure
    deps.github.add_pull_request_comment.assert_awaited_once()
    reply_body = deps.github.add_pull_request_comment.await_args[0][2]
    assert "fix attempt 1 of 3" in reply_body
    assert "FAILED test_foo.py" in reply_body
    assert "NOT pushed" in reply_body

    # Verify store still tracks comment and attempt count
    updated = store.get("123")
    repo_entry = updated["repo_prs"]["your-org/your-repo"]
    assert repo_entry["last_seen_comment_id"] == 55
    assert repo_entry["feedback_attempts"] == 1


@pytest.mark.asyncio
async def test_pr_feedback_caps_at_max_attempts(settings, tmp_path):
    settings.pr_feedback_poll_enabled = True
    settings.change_agent_max_attempts = 3
    store = PageStore(tmp_path / "page_store.json")
    # Already reached 3 attempts
    store.put(_stored_page(last_seen_comment_id=50, feedback_attempts=3))

    deps = _fake_deps(settings, store)
    deps.github.get_pull_request.return_value = PullRequestStatus(
        number=42, state="open", merged=False, labels=[LABEL_AGENT_OPENED]
    )
    deps.github.list_review_comments.return_value = [
        {"id": 60, "user": {"login": "reviewer"}, "body": "Please change again"},
    ]

    with patch("confluence_pr_agent.pipeline.pr_feedback_poller.build_deps", return_value=deps):
        await pr_feedback_poller.check_pr_feedback(settings)

    # Agent must NOT be called
    deps.change_engine.implement_change.assert_not_awaited()
    deps.git.clone.assert_not_awaited()

    # Reply warning that max attempts reached
    deps.github.add_pull_request_comment.assert_awaited_once()
    reply_body = deps.github.add_pull_request_comment.await_args[0][2]
    assert "Maximum feedback fix attempts (3) reached" in reply_body

    # Updated comment id so we don't repeat reply
    updated = store.get("123")
    repo_entry = updated["repo_prs"]["your-org/your-repo"]
    assert repo_entry["last_seen_comment_id"] == 60
    assert repo_entry["feedback_attempts"] == 3

