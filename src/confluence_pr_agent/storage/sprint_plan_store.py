"""Tracks each "Plan a Sprint" batch -- see ui/plan_sprint.py and
jira/sprint_planner.py. Same POC-grade JSON file approach as every other
store here.

One entry per sprint tag. A SprintPlanPage's `phase` is what
pipeline/sprint_runner.py (Phase 3) actually drives forward once every page
in the plan is approved; everything up through "approved" is owned by the
UI/planner (Phase 2).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import NotRequired, TypedDict


class SprintPlanPage(TypedDict):
    page_id: str
    page_title: str
    page_url: str
    # Enough of PageDiff to reconstruct it verbatim at Confirm time (same
    # "bind to what was actually reviewed, don't silently re-fetch" idiom as
    # PendingApproval -- see storage/pending_approval_store.py) rather than
    # re-fetching/re-diffing a page that may have moved between Plan and
    # Confirm.
    page_version: int
    page_body_html: str
    page_labels: list[str]
    previous_version: int | None
    diff_text: str
    is_first_seen: bool
    body_checksum: str
    predicted_labels: list[str]
    applied_labels: list[str]
    # predicted_labels not present in applied_labels -- the early "you're
    # missing a routing label" flag this whole feature exists to surface
    # before any implementation is attempted. Empty = no gap detected.
    label_gap: list[str]
    depends_on_page_ids: list[str]
    dependency_rationale: str
    # Best-effort technical plan from jira/sprint_planner.py -- grounded in
    # each repo's real file tree where GitHubClient.get_repo_file_tree
    # could fetch one (see ui/plan_sprint.py), but never verified against
    # actual file contents, since no repo is cloned at planning time -- see
    # that module's SYSTEM_PROMPT. Each entry: {repo_label, file_path,
    # change, is_new_file}. Grouped by repo_label for display -- see
    # ui/plan_sprint.py::_group_file_changes. Surfaced in both this UI and
    # the Jira story's Implementation Plan section.
    file_changes: list[dict]
    rationale: str
    cross_repo_impact: str
    jira_issue_key: NotRequired[str | None]
    jira_issue_url: NotRequired[str | None]
    # Persisted from jira/story_writer.py's output so the Implementation
    # Plan section (ui/plan_sprint.py::_write_implementation_plan_sections)
    # can rebuild the FULL description -- not just append -- when it runs
    # as a follow-up update after dependency links are known. Absent for
    # any page confirmed before this field existed.
    story_description: NotRequired[str]
    story_acceptance_criteria: NotRequired[list[str]]
    # "planned" (dry run only) -> "confirmed" (story+links written to Jira)
    # -> "approved" (JIRA_APPROVED_STATUS_NAME reached) -> "in_progress" ->
    # "waiting_on_merge" -> "done" -- see pipeline/sprint_runner.py for who
    # drives which transition.
    phase: str
    # {target_repo, pr_number} per repo this page's implementation opened a
    # PR in -- set when phase becomes "waiting_on_merge", polled by
    # pipeline/sprint_runner.py (GitHubClient.get_pull_request) until every
    # one is merged, at which point phase becomes "done". A page whose
    # implementation failed outright (no PR at all) never reaches
    # "waiting_on_merge" -- see sprint_runner.py's error handling.
    merge_pending: NotRequired[list[dict]]


class SprintPlan(TypedDict):
    sprint_tag: str
    gate_label: str
    space_key: str
    created_at: str
    pages: list[SprintPlanPage]
    # Topological order, as a list of page_ids -- computed once at Confirm
    # time (jira/sprint_dependency.py) and then fixed, so a later re-plan
    # doesn't silently reorder a sprint that's already partway through
    # execution.
    order: list[str]


class SprintPlanStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        if not self._path.exists():
            self._write({})

    def _read(self) -> dict[str, SprintPlan]:
        if not self._path.exists():
            return {}
        with self._path.open("r", encoding="utf-8") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}

    def _write(self, data: dict[str, SprintPlan]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp_path.replace(self._path)

    def get(self, sprint_tag: str) -> SprintPlan | None:
        with self._lock:
            return self._read().get(sprint_tag)

    def put(self, plan: SprintPlan) -> None:
        with self._lock:
            data = self._read()
            data[plan["sprint_tag"]] = plan
            self._write(data)

    def list_all(self) -> list[SprintPlan]:
        with self._lock:
            return list(self._read().values())

    def delete(self, sprint_tag: str) -> None:
        with self._lock:
            data = self._read()
            data.pop(sprint_tag, None)
            self._write(data)
