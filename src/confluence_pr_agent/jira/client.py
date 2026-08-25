"""Jira Cloud REST API client -- create/comment/check-status on the story
tracking a page's spec change. Mirrors repo/github_client.py's shape (plain
httpx, Basic Auth), not the `jira` PyPI package -- this project doesn't
depend on it, and the REST surface needed here is small enough not to.
"""

from __future__ import annotations

import httpx

from confluence_pr_agent.jira.adf import build_story_description_adf, text_to_adf
from confluence_pr_agent.models import JiraIssueResult, JiraIssueStatus

API_VERSION = "3"


class JiraClient:
    """`base_url` is the site root, e.g. https://your-team.atlassian.net --
    no /wiki suffix (that's Confluence-specific; Jira Cloud's REST API and
    browse URLs both live at the site root).
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth(email, api_token)
        self._client = http_client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _issue_url(self, key: str) -> str:
        return f"{self._base_url}/browse/{key}"

    async def test_connection(self) -> str:
        """Validates the site URL + credentials via /myself -- the same
        "cheapest authenticated call" idiom as ConfluenceClient.test_connection
        and GitHubClient.test_connection, used by the config UI's "Test
        connection" button. Returns the account's display name.
        """
        resp = await self._client.get(f"{self._base_url}/rest/api/{API_VERSION}/myself", auth=self._auth)
        resp.raise_for_status()
        return resp.json().get("displayName", "")

    async def create_issue(
        self,
        project_key: str,
        issue_type: str,
        summary: str,
        description: str,
        acceptance_criteria: list[str] | None = None,
    ) -> JiraIssueResult:
        resp = await self._client.post(
            f"{self._base_url}/rest/api/{API_VERSION}/issue",
            auth=self._auth,
            json={
                "fields": {
                    "project": {"key": project_key},
                    "issuetype": {"name": issue_type},
                    "summary": summary,
                    "description": build_story_description_adf(description, acceptance_criteria or []),
                }
            },
        )
        resp.raise_for_status()
        key = resp.json()["key"]
        return JiraIssueResult(key=key, url=self._issue_url(key))

    async def update_description(
        self, issue_key: str, description: str, acceptance_criteria: list[str] | None = None
    ) -> None:
        """Refreshes an existing (reused) story's description in place, so it
        reflects the latest spec state instead of staying stuck with whatever
        it said when the story was first created. See pipeline/orchestrator.py
        -- called on the reuse path, alongside a comment recording the exact
        diff, same as a brand-new story gets.
        """
        resp = await self._client.put(
            f"{self._base_url}/rest/api/{API_VERSION}/issue/{issue_key}",
            auth=self._auth,
            json={"fields": {"description": build_story_description_adf(description, acceptance_criteria or [])}},
        )
        resp.raise_for_status()

    async def add_comment(self, issue_key: str, comment: str) -> None:
        resp = await self._client.post(
            f"{self._base_url}/rest/api/{API_VERSION}/issue/{issue_key}/comment",
            auth=self._auth,
            json={"body": text_to_adf(comment)},
        )
        resp.raise_for_status()

    async def get_transitions(self, issue_key: str) -> list[dict]:
        """Raw transition objects ({"id": ..., "name": ..., "to": {...}}) --
        Jira workflow transitions are project/workflow-specific IDs, not
        fixed strings, so a name has to be resolved through this call before
        it can be POSTed. See transition_issue below, and
        pipeline/approval_poller.py / ui/plan_sprint.py's Approve action for
        callers.
        """
        resp = await self._client.get(
            f"{self._base_url}/rest/api/{API_VERSION}/issue/{issue_key}/transitions", auth=self._auth
        )
        resp.raise_for_status()
        return resp.json().get("transitions", [])

    async def transition_issue(self, issue_key: str, transition_name: str) -> bool:
        """Moves `issue_key` through the transition whose name matches
        `transition_name` (case-insensitive), if one is available from its
        current status. Returns False (does nothing) rather than raising
        when no matching transition exists -- a workflow that doesn't offer
        this exact transition from the issue's current state is a
        configuration mismatch the caller should surface as "couldn't
        approve", not a hard error that looks like a network/auth failure.
        """
        transitions = await self.get_transitions(issue_key)
        target = next(
            (t for t in transitions if t.get("name", "").strip().lower() == transition_name.strip().lower()), None
        )
        if target is None:
            return False
        resp = await self._client.post(
            f"{self._base_url}/rest/api/{API_VERSION}/issue/{issue_key}/transitions",
            auth=self._auth,
            json={"transition": {"id": target["id"]}},
        )
        resp.raise_for_status()
        return True

    async def link_issues(self, from_key: str, to_key: str, link_type: str = "Blocks") -> None:
        """Creates a `from_key` {link_type} `to_key` issue link -- e.g.
        link_type="Blocks" makes from_key block to_key. Used by the sprint
        planner (jira/sprint_planner.py) to write the dependency order it
        derived as real Jira issue links, not just internal state --
        `link_type` must be a link-type name that exists in this Jira
        instance (Jira ships "Blocks" by default).
        """
        resp = await self._client.post(
            f"{self._base_url}/rest/api/{API_VERSION}/issueLink",
            auth=self._auth,
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": from_key},
                "outwardIssue": {"key": to_key},
            },
        )
        resp.raise_for_status()

    async def get_issue_status(self, issue_key: str) -> JiraIssueStatus:
        """Fetched fresh each run (not trusted from the page store's cached
        jira_issue_key alone) so a story someone closed/completed is always
        detected before deciding whether to reuse it.
        """
        resp = await self._client.get(
            f"{self._base_url}/rest/api/{API_VERSION}/issue/{issue_key}",
            auth=self._auth,
            params={"fields": "status"},
        )
        resp.raise_for_status()
        data = resp.json()
        status = data["fields"]["status"]
        return JiraIssueStatus(
            key=data["key"], status_name=status["name"], status_category=status["statusCategory"]["key"]
        )
