"""GitHub REST API client for opening/updating pull requests and their
labels.

Branch creation/commit/push is handled by `git_client.GitClient`; this talks
to the GitHub REST API (plain HTTP via httpx) for everything PR-shaped.
"""

from __future__ import annotations

import logging

import httpx

from confluence_pr_agent.models import PullRequestResult, PullRequestStatus

GITHUB_API_BASE = "https://api.github.com"
logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self, token: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client or httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def test_connection(self) -> str:
        """Validates the PAT via /user -- the same "cheapest authenticated
        call" idiom as ConfluenceClient.test_connection and
        JiraClient.test_connection, used by the config UI's "Test connection"
        button. Returns the token's login; raises on an invalid/expired PAT.
        """
        resp = await self._client.get("/user")
        resp.raise_for_status()
        return resp.json().get("login", "")

    async def open_pull_request(
        self,
        owner_repo: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
        draft: bool = False,
    ) -> PullRequestResult:
        resp = await self._client.post(
            f"/repos/{owner_repo}/pulls",
            json={"title": title, "head": head_branch, "base": base_branch, "body": body, "draft": draft},
        )
        resp.raise_for_status()
        data = resp.json()
        return PullRequestResult(number=data["number"], url=data["html_url"], branch=head_branch, draft=data.get("draft", draft))

    async def request_reviewers(self, owner_repo: str, pr_number: int, reviewers: list[str]) -> None:
        if not reviewers:
            return
        try:
            resp = await self._client.post(
                f"/repos/{owner_repo}/pulls/{pr_number}/requested_reviewers",
                json={"reviewers": reviewers},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning(
                "Could not request reviewers %s for %s#%s: %s",
                reviewers, owner_repo, pr_number, exc,
            )

    async def get_pull_request(self, owner_repo: str, pr_number: int) -> PullRequestStatus:
        """Fetched fresh each run (not trusted from the page store's cached
        open_pr_number alone) so a PR merged or closed by a human is always
        detected before deciding whether to reuse its branch.
        """
        resp = await self._client.get(f"/repos/{owner_repo}/pulls/{pr_number}")
        resp.raise_for_status()
        data = resp.json()
        labels = [item["name"] for item in data.get("labels", []) if isinstance(item, dict) and "name" in item]
        return PullRequestStatus(
            number=data["number"],
            state=data["state"],
            merged=bool(data.get("merged", False)),
            labels=labels,
        )

    async def update_pull_request(self, owner_repo: str, pr_number: int, title: str, body: str) -> PullRequestResult:
        resp = await self._client.patch(f"/repos/{owner_repo}/pulls/{pr_number}", json={"title": title, "body": body})
        resp.raise_for_status()
        data = resp.json()
        return PullRequestResult(
            number=data["number"], url=data["html_url"], branch=data["head"]["ref"], draft=data.get("draft", False)
        )

    async def ensure_label(self, owner_repo: str, name: str, color: str, description: str = "") -> None:
        """Creates the label if the repo doesn't already have it. GitHub's
        "add labels to an issue" endpoint 404s on a label name that doesn't
        exist in the repo yet, so this has to run before the first time a
        label is actually applied -- a 422 here just means it already
        exists, which is the common case after the first call.
        """
        resp = await self._client.post(
            f"/repos/{owner_repo}/labels", json={"name": name, "color": color, "description": description}
        )
        if resp.status_code == 422:
            return
        resp.raise_for_status()

    async def add_labels(self, owner_repo: str, pr_number: int, labels: list[str]) -> None:
        if not labels:
            return
        resp = await self._client.post(f"/repos/{owner_repo}/issues/{pr_number}/labels", json={"labels": labels})
        resp.raise_for_status()

    async def remove_label(self, owner_repo: str, pr_number: int, label: str) -> None:
        resp = await self._client.delete(f"/repos/{owner_repo}/issues/{pr_number}/labels/{label}")
        if resp.status_code == 404:
            return  # wasn't on the PR (or repo) -- already the desired state
        resp.raise_for_status()

    async def list_root_files(self, owner_repo: str) -> list[str]:
        """Names of every file/dir at the repo's root, on its default
        branch -- used by ui/routes.py's test-command auto-detect endpoint
        to spot a marker file (pyproject.toml, package.json, pom.xml, ...)
        without a full clone. Raises on a nonexistent/inaccessible repo,
        same as everything else in this client -- the caller decides how to
        degrade (see repo/test_command_detection.py's usage).
        """
        resp = await self._client.get(f"/repos/{owner_repo}/contents/")
        resp.raise_for_status()
        data = resp.json()
        return [item["name"] for item in data] if isinstance(data, list) else []

    async def get_repo_file_tree(self, owner_repo: str, branch: str = "main", limit: int = 400) -> list[str]:
        """Every file path in the repo, via the Git Trees API's recursive
        mode -- one API call, no clone, no file contents. Used by
        jira/sprint_planner.py (through ui/plan_sprint.py) to ground the
        planning agent's file-level predictions in the repo's REAL layout
        instead of guessing plausible-sounding paths from spec text alone.

        Capped at `limit` paths (sorted, so the cap is deterministic, not an
        arbitrary API-order slice) -- this rides along in an LLM prompt, not
        meant to represent every path in a very large repo, just enough
        structure to ground path predictions. Blob (file) entries only, not
        directories -- a path list is more useful to an LLM than a tree with
        directory markers mixed in.
        """
        resp = await self._client.get(f"/repos/{owner_repo}/git/trees/{branch}", params={"recursive": "1"})
        resp.raise_for_status()
        data = resp.json()
        paths = sorted(item["path"] for item in data.get("tree", []) if item.get("type") == "blob")
        return paths[:limit]

    async def list_review_comments(
        self, owner_repo: str, pr_number: int, since: str | None = None,
    ) -> list[dict]:
        """Fetches PR review comments. Returns raw GitHub API comment dicts.
        ``since`` is an ISO-8601 timestamp that filters to comments created or
        updated at or after that time.
        """
        params: dict[str, str] = {"per_page": "100"}
        if since:
            params["since"] = since
        resp = await self._client.get(
            f"/repos/{owner_repo}/pulls/{pr_number}/comments", params=params,
        )
        resp.raise_for_status()
        return resp.json()
    async def add_pull_request_comment(
        self, owner_repo: str, pr_number: int, body: str,
    ) -> dict:
        """Posts a comment on the PR's main conversation thread."""
        resp = await self._client.post(
            f"/repos/{owner_repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()
