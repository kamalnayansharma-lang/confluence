"""Confluence Cloud REST API client."""

from __future__ import annotations

import httpx

from confluence_pr_agent.models import PageSnapshot


def _cql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class ConfluenceClient:
    """Thin wrapper around the Confluence Cloud content API.

    `base_url` should be the site's `/wiki` root, e.g.
    https://your-team.atlassian.net/wiki
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

    async def test_connection(self) -> str:
        """Validates the base URL + credentials via the cheapest authenticated
        call Confluence Cloud's REST API offers -- no space or page needed.
        Used by the config UI's "Test connection" button so a bad token or
        wrong /wiki URL surfaces here instead of silently failing deep into a
        real pipeline run. Returns the account's display name; raises (like
        every other method here) on failure, leaving status-code -> message
        translation to the caller.
        """
        resp = await self._client.get(f"{self._base_url}/rest/api/user/current", auth=self._auth)
        resp.raise_for_status()
        return resp.json().get("displayName", "")

    async def fetch_page(self, page_id: str) -> PageSnapshot:
        """Fetch the current storage-format body + version + labels for a page.

        Labels come along via expand=metadata.labels rather than a separate
        /label request -- one API call either way, and it's what
        CONFLUENCE_ALLOWED_LABELS filtering (orchestrator.py) is checked
        against before anything else happens for a page.
        """
        resp = await self._client.get(
            f"{self._base_url}/rest/api/content/{page_id}",
            params={"expand": "body.storage,version,metadata.labels"},
            auth=self._auth,
        )
        resp.raise_for_status()
        data = resp.json()

        body_html = data["body"]["storage"]["value"]
        version = data["version"]["number"]
        title = data["title"]
        webui_path = data.get("_links", {}).get("webui", f"/pages/{data['id']}")
        page_url = f"{self._base_url}{webui_path}"
        label_results = data.get("metadata", {}).get("labels", {}).get("results", [])
        labels = [label["name"] for label in label_results if label.get("name")]

        return PageSnapshot(
            page_id=str(data["id"]),
            title=title,
            version=version,
            body_html=body_html,
            url=page_url,
            labels=labels,
        )

    async def search_page_ids(self, space_key: str, labels: list[str] | None = None) -> list[str]:
        """Finds every page in `space_key` carrying at least one of `labels`,
        via CQL search -- the discovery half of polling (pipeline/poller.py).
        No labels means no filter: every page in the space, same "empty =
        everyone" semantics as CONFLUENCE_ALLOWED_LABELS on the webhook path.
        """
        clauses = [f'space="{_cql_escape(space_key)}"', "type=page"]
        if labels:
            label_clause = " OR ".join(f'label="{_cql_escape(label)}"' for label in labels)
            clauses.append(f"({label_clause})")
        cql = " AND ".join(clauses)

        page_ids: list[str] = []
        url = f"{self._base_url}/rest/api/content/search"
        params: dict | None = {"cql": cql, "limit": 100}
        # Bounded against a runaway `next` chain -- a space with more pages
        # than this matching in one poll cycle isn't this POC's use case.
        for _ in range(20):
            resp = await self._client.get(url, params=params, auth=self._auth)
            resp.raise_for_status()
            data = resp.json()
            page_ids.extend(str(r["id"]) for r in data.get("results", []))

            next_path = data.get("_links", {}).get("next")
            if not next_path:
                break
            url = f"{self._base_url}{next_path}"
            params = None  # already encoded into next_path's query string

        return page_ids

    async def search_page_ids_all_labels(self, space_key: str, required_labels: list[str]) -> list[str]:
        """Like search_page_ids, but ANDs every label together instead of
        ORing them -- "must have all of these", not "must have at least
        one". Used by the sprint planner (ui/plan_sprint.py) to find pages
        carrying both the gate label and a specific sprint label;
        search_page_ids's OR semantics are what the general poller
        (pipeline/poller.py) wants instead, so this is a distinct method
        rather than an overloaded flag on that one.
        """
        clauses = [f'space="{_cql_escape(space_key)}"', "type=page"]
        clauses.extend(f'label="{_cql_escape(label)}"' for label in required_labels)
        cql = " AND ".join(clauses)

        page_ids: list[str] = []
        url = f"{self._base_url}/rest/api/content/search"
        params: dict | None = {"cql": cql, "limit": 100}
        for _ in range(20):
            resp = await self._client.get(url, params=params, auth=self._auth)
            resp.raise_for_status()
            data = resp.json()
            page_ids.extend(str(r["id"]) for r in data.get("results", []))

            next_path = data.get("_links", {}).get("next")
            if not next_path:
                break
            url = f"{self._base_url}{next_path}"
            params = None

        return page_ids

    async def search_pages_by_text(self, space_key: str, query: str, limit: int = 5) -> list[str]:
        """Free-text CQL search (title/body), for jira/story_writer.py's
        "related prior work" fallback when a page has no explicit inline
        links to related content (see confluence/links.py). Returns page
        ids only -- the caller fetches full content for whichever ones it
        actually wants via fetch_page, same as every other search method
        here.
        """
        query = query.strip()
        if not query:
            return []
        cql = f'space="{_cql_escape(space_key)}" AND type=page AND text ~ "{_cql_escape(query)}"'
        resp = await self._client.get(
            f"{self._base_url}/rest/api/content/search",
            params={"cql": cql, "limit": limit},
            auth=self._auth,
        )
        resp.raise_for_status()
        return [str(r["id"]) for r in resp.json().get("results", [])]
