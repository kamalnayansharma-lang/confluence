"""Assembles bounded "related prior work" context from other Confluence
pages, for both the Jira story writer and the coding agent's prompt (see
PageDiff.related_context, set once in pipeline/orchestrator.py and read by
jira/story_writer.py::build_prompt and agent/prompts.py::build_user_prompt).

Explicit in-body links (confluence/links.py) are preferred over a keyword
search -- the page's own author put them there, so they're higher-confidence
than anything a text search would guess at. Capped hard (few pages, short
excerpts) since this rides along on every run's prompt, not just the ones
that would actually benefit from it.
"""

from __future__ import annotations

import logging

from confluence_pr_agent.confluence.client import ConfluenceClient
from confluence_pr_agent.confluence.diff import _to_plain_text
from confluence_pr_agent.confluence.links import extract_linked_page_ids
from confluence_pr_agent.models import PageSnapshot

logger = logging.getLogger(__name__)

_MAX_RELATED_PAGES = 3
_EXCERPT_CHARS = 400


async def gather_related_context(
    confluence: ConfluenceClient, space_key: str, page: PageSnapshot
) -> str | None:
    """Best-effort: any failure here (a broken link, a search error) is
    logged and swallowed -- this is enrichment, not something that should
    ever be able to fail a pipeline run. Returns None when nothing relevant
    is found, same as when nothing was configured at all.
    """
    try:
        page_ids = extract_linked_page_ids(page.body_html, exclude_page_id=page.page_id)
        source = "linked from this page"
        if not page_ids:
            page_ids = await confluence.search_pages_by_text(space_key, page.title, limit=_MAX_RELATED_PAGES)
            page_ids = [pid for pid in page_ids if pid != page.page_id]
            source = "found by title search"
        if not page_ids:
            return None

        blocks: list[str] = []
        for related_id in page_ids[:_MAX_RELATED_PAGES]:
            try:
                related = await confluence.fetch_page(related_id)
            except Exception as exc:
                logger.warning("Could not fetch related page %s: %s", related_id, exc)
                continue
            excerpt = _to_plain_text(related.body_html)[:_EXCERPT_CHARS]
            blocks.append(f"- \"{related.title}\" ({related.url}, {source}):\n  {excerpt}")

        return "\n\n".join(blocks) if blocks else None
    except Exception as exc:
        logger.warning("Failed to gather related context for page %s: %s", page.page_id, exc)
        return None
