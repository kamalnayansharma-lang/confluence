"""Extracts ids of other Confluence pages explicitly linked from a page's
storage-format body -- higher-confidence "related prior work" than a
keyword search, since the page's own author put the link there. Must run
BEFORE confluence/diff.py::_to_plain_text, which strips all markup
(including these links) on its way to producing the agent's diff text.

Two link shapes appear in Confluence storage format:
- A plain anchor to another page's web UI path: <a href="/wiki/spaces/SD/pages/12345/Some-Title">
- A native Confluence page-link macro: <ac:link><ri:page ri:content-id="12345" /></ac:link>
"""

from __future__ import annotations

import re

_HREF_PAGE_ID = re.compile(r'href="[^"]*?/pages/(\d+)(?:/|")')
_AC_LINK_CONTENT_ID = re.compile(r'ri:content-id="(\d+)"')


def extract_linked_page_ids(body_html: str, exclude_page_id: str | None = None) -> list[str]:
    """Deduplicated, in first-seen order. `exclude_page_id` drops a
    self-link (a page linking to itself, e.g. a "see also" section edited
    before the page had its own id in hand) from the result.
    """
    found: list[str] = []
    seen: set[str] = set()
    for pattern in (_HREF_PAGE_ID, _AC_LINK_CONTENT_ID):
        for match in pattern.finditer(body_html):
            page_id = match.group(1)
            if page_id == exclude_page_id or page_id in seen:
                continue
            seen.add(page_id)
            found.append(page_id)
    return found
