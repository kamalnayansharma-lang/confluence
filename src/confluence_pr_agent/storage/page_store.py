"""Tracks the last-seen version/body of each watched Confluence page.

POC-grade JSON file store. Good enough for a single-process deployment;
swap for SQLite (or a real DB) if you need multi-process/concurrent writers.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import NotRequired, TypedDict


class StoredPage(TypedDict):
    page_id: str
    title: str
    version: int
    body_html: str
    body_checksum: str  # sha256 of the normalized plain-text body; see confluence/diff.py
    url: str
    # Tracks the most recent PR opened for this page, if it's still open --
    # lets the next run reuse/update it instead of opening a duplicate PR
    # when the spec changes again before that PR is merged. Absent/None once
    # that PR is merged or closed (checked fresh each run via the GitHub
    # API, not assumed from this cache alone). See pipeline/orchestrator.py.
    open_pr_number: NotRequired[int | None]
    open_pr_branch: NotRequired[str | None]
    # Same idea as open_pr_number/open_pr_branch above, but keyed per repo
    # (target_repo -> {"open_pr_number": int, "open_pr_branch": str}) for a
    # multi-repo run (see config.py::Settings.resolved_repo_targets and
    # pipeline/orchestrator.py) -- one page can now have an open PR in
    # several repos at once. open_pr_number/open_pr_branch above stay as the
    # single-repo fallback's own record (read first for any page that
    # predates this field, or that's never been anything but single-repo).
    repo_prs: NotRequired[dict[str, dict]]
    # Same idea, for the Jira story tracking this page (JIRA_ENABLED) -- the
    # next run comments on it instead of creating a duplicate while it's
    # still open. See pipeline/orchestrator.py.
    jira_issue_key: NotRequired[str | None]


class PageStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        if not self._path.exists():
            self._write({})

    def _read(self) -> dict[str, StoredPage]:
        if not self._path.exists():
            return {}
        with self._path.open("r", encoding="utf-8") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}

    def _write(self, data: dict[str, StoredPage]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp_path.replace(self._path)

    def get(self, page_id: str) -> StoredPage | None:
        with self._lock:
            return self._read().get(page_id)

    def put(self, page: StoredPage) -> None:
        with self._lock:
            data = self._read()
            data[page["page_id"]] = page
            self._write(data)

    def remember_jira_issue(self, page_id: str, issue_key: str) -> None:
        """Persists just the Jira story key for this page, independent of
        whether the rest of the pipeline run succeeds -- so a run that
        creates (or finds) a story but then fails downstream (change engine
        crash, failing tests, ...) doesn't lose track of it and create a
        duplicate on the next retry, before put() above ever gets called.
        Deliberately a merge, not put()'s full replace: version/
        body_checksum/body_html/open_pr_* are left untouched, so
        confluence/diff.py::compute_diff still correctly treats this page as
        unresolved and keeps retrying until a run actually succeeds through
        to put(). No-op if there's no prior record at all yet -- this page's
        very first version, first run ever -- since a full record needs a
        real version/body_checksum/body_html and put() is what supplies
        those; that specific edge case (a story created on the very first
        run, which then fails before put()) stays unfixed, but it's rare
        enough (every page here has necessarily gone through at least one
        successful run already once it has any config at all) not to be
        worth a synthetic placeholder record.
        """
        with self._lock:
            data = self._read()
            existing = data.get(page_id)
            if existing is None:
                return
            existing["jira_issue_key"] = issue_key
            data[page_id] = existing
            self._write(data)

    def list_all(self) -> list[StoredPage]:
        with self._lock:
            return list(self._read().values())

    def update_repo_pr_field(self, page_id: str, target_repo: str, key: str, value: object) -> None:
        """Merges a single key into the repo_prs[target_repo] dict for a page,
        without overwriting the rest of the record -- used by the PR feedback
        poller to track last_seen_comment_id and feedback_attempts.
        """
        with self._lock:
            data = self._read()
            page = data.get(page_id)
            if page is None:
                return
            repo_prs = page.get("repo_prs")
            if repo_prs is None:
                repo_prs = {}
            entry = dict(repo_prs.get(target_repo) or {})
            # Migrate the pre-multi-repo single-PR fields when the feedback
            # loop first persists per-PR state. Without this, writing
            # last_seen_comment_id would make the legacy PR undiscoverable on
            # the next poll because repo_prs would exist without its number.
            if not entry and not repo_prs:
                legacy_number = page.get("open_pr_number")
                legacy_branch = page.get("open_pr_branch")
                if legacy_number and legacy_branch:
                    entry.update(
                        open_pr_number=legacy_number,
                        open_pr_branch=legacy_branch,
                    )
            entry[key] = value
            repo_prs[target_repo] = entry
            page["repo_prs"] = repo_prs
            data[page_id] = page
            self._write(data)
