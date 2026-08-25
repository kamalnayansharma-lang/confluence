"""Tracks Confluence pages whose spec change has a Jira story created but is
waiting on JIRA_APPROVAL_REQUIRED before implementation starts -- see
pipeline/orchestrator.py's split into run_pipeline/_implement_change and
pipeline/approval_poller.py, which is what actually resumes these.

Same POC-grade JSON file approach as PageStore/RunStore.

Approval binds to the *exact* diff that was reviewed, not whatever the page
says by the time approval lands -- so this stores enough of PageDiff to
reconstruct it verbatim, plus the page snapshot's version/checksum at the
time the story was created, which approval_poller.py compares against a
fresh fetch to detect an edit that happened while a story was pending (see
that module's docstring for what happens then).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import NotRequired, TypedDict


class PendingApproval(TypedDict):
    page_id: str
    jira_issue_key: str
    jira_issue_url: str
    # The PageDiff fields needed to resume _implement_change verbatim --
    # see models.py::PageDiff. page_version/page_body_checksum below are the
    # page's OWN state at story-creation time (distinct from
    # previous_version, which is the version *before* this diff), used to
    # detect a same-page edit that happened while this entry is pending.
    previous_version: int | None
    diff_text: str
    is_first_seen: bool
    body_checksum: str
    page_version: int
    page_body_checksum: str
    page_title: str
    page_url: str
    page_body_html: str
    page_labels: list[str]
    run_id: str  # the original run_pipeline invocation's run_id, reused on resume
    created_at: str  # ISO 8601
    # Set (non-empty) once approval_poller.py notices the page changed again
    # while this entry was pending -- see that module for what happens then.
    # Left in place (not deleted) rather than auto-cleared so a human can see
    # in /ui/plan-sprint or a future admin view that this entry needs a look,
    # not just silently keep polling forever on stale content.
    stale_reason: NotRequired[str | None]


class PendingApprovalStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        if not self._path.exists():
            self._write({})

    def _read(self) -> dict[str, PendingApproval]:
        if not self._path.exists():
            return {}
        with self._path.open("r", encoding="utf-8") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}

    def _write(self, data: dict[str, PendingApproval]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp_path.replace(self._path)

    def get(self, page_id: str) -> PendingApproval | None:
        with self._lock:
            return self._read().get(page_id)

    def put(self, entry: PendingApproval) -> None:
        with self._lock:
            data = self._read()
            data[entry["page_id"]] = entry
            self._write(data)

    def delete(self, page_id: str) -> None:
        with self._lock:
            data = self._read()
            data.pop(page_id, None)
            self._write(data)

    def list_all(self) -> list[PendingApproval]:
        with self._lock:
            return list(self._read().values())
