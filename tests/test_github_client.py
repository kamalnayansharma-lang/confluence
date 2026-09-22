from __future__ import annotations

import json

import httpx
import respx

from confluence_pr_agent.repo.github_client import GitHubClient


@respx.mock
async def test_open_pull_request_returns_result():
    respx.post("https://api.github.com/repos/acme/widgets/pulls").mock(
        return_value=httpx.Response(
            201, json={"number": 42, "html_url": "https://github.com/acme/widgets/pull/42"}
        )
    )

    client = GitHubClient(token="test-token")
    result = await client.open_pull_request(
        owner_repo="acme/widgets",
        head_branch="confluence-sync/page-1-v2",
        base_branch="main",
        title="Sync with Confluence",
        body="body",
    )
    await client.aclose()

    assert result.number == 42
    assert result.url == "https://github.com/acme/widgets/pull/42"
    assert result.branch == "confluence-sync/page-1-v2"
    assert result.draft is False


@respx.mock
async def test_open_pull_request_draft_true_sends_draft_flag_and_reflects_response():
    route = respx.post("https://api.github.com/repos/acme/widgets/pulls").mock(
        return_value=httpx.Response(
            201,
            json={"number": 43, "html_url": "https://github.com/acme/widgets/pull/43", "draft": True},
        )
    )

    client = GitHubClient(token="test-token")
    result = await client.open_pull_request(
        owner_repo="acme/widgets",
        head_branch="confluence-sync/page-1-v3",
        base_branch="main",
        title="[Needs Work] Sync with Confluence",
        body="body",
        draft=True,
    )
    await client.aclose()

    assert json.loads(route.calls.last.request.content)["draft"] is True
    assert result.draft is True


@respx.mock
async def test_request_reviewers_posts_reviewer_logins():
    route = respx.post(
        "https://api.github.com/repos/acme/widgets/pulls/42/requested_reviewers"
    ).mock(return_value=httpx.Response(201, json={"requested_reviewers": []}))

    client = GitHubClient(token="test-token")
    await client.request_reviewers("acme/widgets", 42, ["octocat"])
    await client.aclose()

    assert json.loads(route.calls.last.request.content) == {"reviewers": ["octocat"]}


@respx.mock
async def test_request_reviewers_fails_open_when_github_rejects_reviewer():
    respx.post("https://api.github.com/repos/acme/widgets/pulls/42/requested_reviewers").mock(
        return_value=httpx.Response(422)
    )

    client = GitHubClient(token="test-token")
    await client.request_reviewers("acme/widgets", 42, ["not-a-collaborator"])
    await client.aclose()


@respx.mock
async def test_get_pull_request_reports_open_state():
    respx.get("https://api.github.com/repos/acme/widgets/pulls/42").mock(
        return_value=httpx.Response(200, json={"number": 42, "state": "open", "merged": False})
    )

    client = GitHubClient(token="test-token")
    status = await client.get_pull_request("acme/widgets", 42)
    await client.aclose()

    assert status.number == 42
    assert status.is_open is True
    assert status.merged is False


@respx.mock
async def test_get_pull_request_reports_merged_as_not_open():
    respx.get("https://api.github.com/repos/acme/widgets/pulls/42").mock(
        return_value=httpx.Response(200, json={"number": 42, "state": "closed", "merged": True})
    )

    client = GitHubClient(token="test-token")
    status = await client.get_pull_request("acme/widgets", 42)
    await client.aclose()

    assert status.is_open is False
    assert status.merged is True


@respx.mock
async def test_update_pull_request_patches_title_and_body():
    route = respx.patch("https://api.github.com/repos/acme/widgets/pulls/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 42,
                "html_url": "https://github.com/acme/widgets/pull/42",
                "head": {"ref": "confluence-sync/page-1-v2"},
                "draft": False,
            },
        )
    )

    client = GitHubClient(token="test-token")
    result = await client.update_pull_request("acme/widgets", 42, title="New title", body="New body")
    await client.aclose()

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"title": "New title", "body": "New body"}
    assert result.number == 42
    assert result.branch == "confluence-sync/page-1-v2"


@respx.mock
async def test_ensure_label_treats_already_exists_as_success():
    respx.post("https://api.github.com/repos/acme/widgets/labels").mock(return_value=httpx.Response(422))

    client = GitHubClient(token="test-token")
    await client.ensure_label("acme/widgets", "agent:needs-work", "d73a4a")  # must not raise
    await client.aclose()


@respx.mock
async def test_ensure_label_creates_label_when_missing():
    route = respx.post("https://api.github.com/repos/acme/widgets/labels").mock(
        return_value=httpx.Response(201, json={"name": "agent:needs-work"})
    )

    client = GitHubClient(token="test-token")
    await client.ensure_label("acme/widgets", "agent:needs-work", "d73a4a", "desc")
    await client.aclose()

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"name": "agent:needs-work", "color": "d73a4a", "description": "desc"}


@respx.mock
async def test_add_labels_posts_to_issue_labels_endpoint():
    route = respx.post("https://api.github.com/repos/acme/widgets/issues/42/labels").mock(
        return_value=httpx.Response(200, json=[])
    )

    client = GitHubClient(token="test-token")
    await client.add_labels("acme/widgets", 42, ["agent:needs-work"])
    await client.aclose()

    assert json.loads(route.calls.last.request.content) == {"labels": ["agent:needs-work"]}


@respx.mock
async def test_add_labels_is_a_noop_for_an_empty_list():
    route = respx.post("https://api.github.com/repos/acme/widgets/issues/42/labels")

    client = GitHubClient(token="test-token")
    await client.add_labels("acme/widgets", 42, [])
    await client.aclose()

    assert route.calls.call_count == 0


@respx.mock
async def test_remove_label_treats_not_found_as_success():
    respx.delete("https://api.github.com/repos/acme/widgets/issues/42/labels/agent:warning").mock(
        return_value=httpx.Response(404)
    )

    client = GitHubClient(token="test-token")
    await client.remove_label("acme/widgets", 42, "agent:warning")  # must not raise
    await client.aclose()


@respx.mock
async def test_list_review_comments_fetches_pull_request_comments():
    route = respx.get("https://api.github.com/repos/acme/widgets/pulls/42/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 101, "body": "Looks good", "user": {"login": "alice", "type": "User"}},
                {"id": 102, "body": "Fix the bug", "user": {"login": "octocat", "type": "Bot"}},
            ],
        )
    )

    client = GitHubClient(token="test-token")
    comments = await client.list_review_comments("acme/widgets", 42)
    await client.aclose()

    assert [c["id"] for c in comments] == [101, 102]
    assert route.calls.last.request.url.params["per_page"] == "100"


@respx.mock
async def test_add_pull_request_comment_posts_to_issue_comments_endpoint():
    route = respx.post("https://api.github.com/repos/acme/widgets/issues/42/comments").mock(
        return_value=httpx.Response(201, json={"id": 99})
    )

    client = GitHubClient(token="test-token")
    await client.add_pull_request_comment("acme/widgets", 42, "Fixed in follow-up commit.")
    await client.aclose()

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"body": "Fixed in follow-up commit."}
