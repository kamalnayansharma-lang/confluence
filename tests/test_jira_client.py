from __future__ import annotations

import httpx

from confluence_pr_agent.jira.client import JiraClient
from confluence_pr_agent.config import Settings


def test_settings_adds_https_to_host_only_jira_url():
    settings = Settings(jira_base_url="jira.example.com")

    assert settings.jira_base_url == "https://jira.example.com"


async def test_host_only_jira_url_is_normalized_before_request():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"displayName": "Demo User"})

    client = JiraClient(
        "jira.example.com",
        "user@example.com",
        "token",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        assert await client.test_connection() == "Demo User"
    finally:
        await client._client.aclose()

    assert str(requests[0].url) == "https://jira.example.com/rest/api/3/myself"


async def test_search_issues_uses_current_jira_search_jql_endpoint():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "issues": [
                    {
                        "key": "KAN-42",
                        "fields": {
                            "summary": "Broken checkout",
                            "description": "Checkout fails.",
                            "status": {"name": "Open"},
                            "issuetype": {"name": "Bug"},
                            "priority": {"name": "High"},
                            "labels": ["production"],
                        },
                    }
                ]
            },
        )

    client = JiraClient(
        "https://jira.example.com",
        "user@example.com",
        "token",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        issues = await client.search_issues("project = KAN", limit=10)
    finally:
        await client._client.aclose()

    assert requests[0].method == "POST"
    assert str(requests[0].url) == "https://jira.example.com/rest/api/3/search/jql"
    assert requests[0].content == (
        b'{"jql":"project = KAN","maxResults":10,"fields":["summary","description","status",'
        b'"issuetype","priority","labels"]}'
    )
    assert issues[0]["key"] == "KAN-42"
    assert issues[0]["issue_type"] == "Bug"