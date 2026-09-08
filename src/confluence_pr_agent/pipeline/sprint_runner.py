"""Executes an approved, ordered sprint plan (storage/sprint_plan_store.py)
one page at a time, gated on the previous page's PR(s) actually being
merged before the next dependent page starts -- see the module-level design
note below for why merge-gating, not just topological order, is what keeps
cross-page consistency correct.

`run_pipeline` and `_implement_change`-equivalent logic in
pipeline/orchestrator.py need NO changes for this: this runner only decides
*when* to call the existing, unmodified `run_pipeline(page_id, deps,
resume=...)` for one page at a time, in what order -- the exact same
`resume` mechanism pipeline/approval_poller.py already uses, since a sprint
page's approval is tracked identically to a single-page HITL approval (see
storage/sprint_plan_store.py's SprintPlanPage, which carries the same diff
snapshot fields as PendingApproval).

Why merge-gating: if page B's agent cloned while page A's PR was still
open, B would never see A's changes -- they only exist on an unmerged
branch. Waiting for A to actually merge before B starts means B's agent
just reads A's real, reviewed code off the now-updated base branch, with no
need to fabricate "here's what A did" context into B's prompt.

Independent, non-blocking pages in the same sprint still execute strictly
serially (one in-flight page per sprint at a time) -- a declared-dependency
graph only reflects what jira/sprint_planner.py proposed and a human
confirmed; it says nothing about two undeclared-independent pages that
happen to touch the same repo. Parallelizing genuinely safe cases is a
fast-follow, not this module's job.
"""

from __future__ import annotations

import asyncio
import logging
import time

from confluence_pr_agent.config import Settings, get_process_config, get_settings
from confluence_pr_agent.pipeline.orchestrator import build_deps, run_pipeline
from confluence_pr_agent.storage.pending_approval_store import PendingApproval
from confluence_pr_agent.storage.sprint_plan_store import SprintPlan, SprintPlanPage, SprintPlanStore

logger = logging.getLogger(__name__)

SPRINT_RUNNER_TICK_SECONDS = 30


def _pages_by_id(plan: SprintPlan) -> dict[str, SprintPlanPage]:
    return {p["page_id"]: p for p in plan["pages"]}


def _next_ready_page(plan: SprintPlan) -> SprintPlanPage | None:
    """The first page (in the plan's fixed topological order) that's
    approved, whose every dependency has already reached "done" -- and only
    if nothing in this plan is currently in_progress/waiting_on_merge (the
    strict-serial-per-sprint rule described in the module docstring).
    """
    pages = _pages_by_id(plan)
    if any(p["phase"] in ("in_progress", "waiting_on_merge") for p in pages.values()):
        return None
    for page_id in plan["order"]:
        page = pages.get(page_id)
        if page is None or page["phase"] != "approved":
            continue
        if all(pages.get(dep, {}).get("phase") == "done" for dep in page["depends_on_page_ids"]):
            return page
    return None


def _as_resume(page: SprintPlanPage) -> PendingApproval:
    """SprintPlanPage carries the same diff-snapshot shape PendingApproval
    does (see storage/sprint_plan_store.py) -- reuses run_pipeline's
    existing `resume` mechanism verbatim rather than inventing a second
    "implement from a saved snapshot" path.
    """
    return {
        "page_id": page["page_id"],
        "jira_issue_key": page.get("jira_issue_key") or "",
        "jira_issue_url": page.get("jira_issue_url") or "",
        "previous_version": page["previous_version"],
        "diff_text": page["diff_text"],
        "is_first_seen": page["is_first_seen"],
        "body_checksum": page["body_checksum"],
        "page_version": page["page_version"],
        "page_body_checksum": page["body_checksum"],
        "page_title": page["page_title"],
        "page_url": page["page_url"],
        "page_body_html": page["page_body_html"],
        "page_labels": page["page_labels"],
        "run_id": "",
        "created_at": "",
    }


async def _advance_ready_page(settings: Settings, plan_store: SprintPlanStore, plan: SprintPlan, page: SprintPlanPage) -> None:
    page["phase"] = "in_progress"
    plan_store.put(plan)

    deps = build_deps(settings)
    try:
        result = await run_pipeline(page["page_id"], deps=deps, resume=_as_resume(page))
        opened = [r for r in result.repo_results if r.pull_request]
        if not opened:
            logger.warning(
                "Sprint %s page %s implementation produced no PR (status=%s) -- marking failed. A human "
                "must retry it explicitly (Sprint Planning); the scan loop won't re-attempt it on its own, "
                "since that would silently re-spend a real LLM call every tick.",
                plan["sprint_tag"], page["page_id"], result.status,
            )
            page["phase"] = "failed"
            page["last_error"] = f"No PR opened (status={result.status}). See Runs for this page for details."
        else:
            page["merge_pending"] = [{"target_repo": r.target_repo, "pr_number": r.pull_request.number} for r in opened]
            page["phase"] = "waiting_on_merge"
            page["last_error"] = None
        plan_store.put(plan)
    except Exception as exc:
        logger.exception("Sprint %s page %s implementation failed", plan["sprint_tag"], page["page_id"])
        # "failed", not "approved" -- see the phase docstring in
        # storage/sprint_plan_store.py for why this must NOT be auto-retried.
        page["phase"] = "failed"
        page["last_error"] = str(exc)
        plan_store.put(plan)
    finally:
        await deps.confluence.aclose()
        await deps.github.aclose()
        await deps.email_client.aclose()
        await deps.jira.aclose()


async def _check_merge_status(settings: Settings, plan_store: SprintPlanStore, plan: SprintPlan, page: SprintPlanPage) -> None:
    deps = build_deps(settings)
    try:
        all_merged = True
        for pr in page.get("merge_pending") or []:
            try:
                status = await deps.github.get_pull_request(pr["target_repo"], pr["pr_number"])
            except Exception as exc:
                logger.warning(
                    "Could not check merge status of %s#%s for sprint %s page %s: %s",
                    pr["target_repo"], pr["pr_number"], plan["sprint_tag"], page["page_id"], exc,
                )
                all_merged = False
                continue
            if not status.merged:
                all_merged = False
        if all_merged:
            logger.info("Sprint %s page %s: all PRs merged.", plan["sprint_tag"], page["page_id"])
            page["phase"] = "done"
            plan_store.put(plan)
    finally:
        await deps.github.aclose()
        await deps.confluence.aclose()
        await deps.email_client.aclose()
        await deps.jira.aclose()


async def _check_confirmed_approvals(settings: Settings, plan_store: SprintPlanStore, plan: SprintPlan) -> None:
    """Mirrors pipeline/approval_poller.py's own live-status check, for
    sprint-planned pages: a story can be approved directly in Jira (not just
    via this app's own per-row/batch Approve button in Sprint Planning),
    which only updates the real Jira ticket -- nothing here notices unless
    something independently checks Jira's live status. Without this,
    _next_ready_page's `phase == "approved"` gate never advances for a page
    approved this way, since that field is a local cache this module itself
    only ever wrote when someone clicked Approve *in this app*; a story
    approved directly in Jira left it stuck at "confirmed" forever.
    """
    approved_name = settings.jira_approved_status_name.strip().lower()
    if not approved_name:
        return
    deps = build_deps(settings)
    changed = False
    try:
        for page in plan["pages"]:
            if page["phase"] != "confirmed" or not page.get("jira_issue_key"):
                continue
            try:
                status = await deps.jira.get_issue_status(page["jira_issue_key"])
            except Exception as exc:
                logger.warning(
                    "Could not check Jira status of %s for sprint %s page %s: %s",
                    page["jira_issue_key"], plan["sprint_tag"], page["page_id"], exc,
                )
                continue
            if status.status_name.strip().lower() == approved_name:
                logger.info(
                    "Sprint %s page %s: story %s reached %r directly in Jira; marking approved.",
                    plan["sprint_tag"], page["page_id"], page["jira_issue_key"], status.status_name,
                )
                page["phase"] = "approved"
                changed = True
        if changed:
            plan_store.put(plan)
    finally:
        await deps.confluence.aclose()
        await deps.github.aclose()
        await deps.email_client.aclose()
        await deps.jira.aclose()


async def check_sprint_plans(settings: Settings) -> None:
    """One check cycle for one user: sync any directly-in-Jira approvals,
    advance the next ready page in every plan that has one, and check merge
    status for every page currently waiting on one. Sequential across plans
    -- sprint execution is deliberately not a race for throughput.
    """
    plan_store = SprintPlanStore(settings.sprint_plan_store_path)
    for plan in plan_store.list_all():
        await _check_confirmed_approvals(settings, plan_store, plan)
        plan = plan_store.get(plan["sprint_tag"]) or plan

        for page in plan["pages"]:
            if page["phase"] == "waiting_on_merge":
                await _check_merge_status(settings, plan_store, plan, page)

        # Re-fetch: a merge check above may have just freed this plan up
        # for its next page within the same tick.
        plan = plan_store.get(plan["sprint_tag"]) or plan
        ready = _next_ready_page(plan)
        if ready is not None:
            await _advance_ready_page(settings, plan_store, plan, ready)


async def sprint_runner_scan_loop() -> None:
    """Started once from webhook/app.py's lifespan, same per-user directory
    scan shape as poller.py/approval_poller.py.
    """
    last_checked: dict[str, float] = {}
    process = get_process_config()
    while True:
        try:
            if process.users_dir_path.exists():
                for user_dir in process.users_dir_path.iterdir():
                    if not user_dir.is_dir():
                        continue
                    username = user_dir.name
                    now = time.monotonic()
                    last = last_checked.get(username)
                    if last is not None and now - last < SPRINT_RUNNER_TICK_SECONDS:
                        continue
                    last_checked[username] = now
                    asyncio.create_task(check_sprint_plans(get_settings(username)))
        except Exception:
            logger.exception("Sprint runner scan tick failed")
        await asyncio.sleep(SPRINT_RUNNER_TICK_SECONDS)
