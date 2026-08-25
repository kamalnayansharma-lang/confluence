"""Resumes implementation for pages whose Jira story has been waiting on
JIRA_APPROVAL_REQUIRED -- the counterpart to pipeline/poller.py, which
*discovers* changed pages; this one watches PendingApprovalStore entries
that pipeline/orchestrator.py::run_pipeline already created and decides when
each is ready to continue (see run_pipeline's `resume` parameter).

Same per-process-scan shape as poller.py::poll_scan_loop (one task started
once from webhook/app.py's lifespan, scanning every provisioned user's own
pending approvals on a timer) rather than N per-user tasks, for the same
reasons that module gives: simpler lifecycle, deprovisioning is free.
"""

from __future__ import annotations

import asyncio
import logging
import time

from confluence_pr_agent.confluence.diff import _to_plain_text, compute_checksum
from confluence_pr_agent.config import Settings, get_process_config, get_settings
from confluence_pr_agent.pipeline.orchestrator import build_deps, run_pipeline
from confluence_pr_agent.storage.pending_approval_store import PendingApproval

logger = logging.getLogger(__name__)

APPROVAL_POLL_TICK_SECONDS = 30


async def _check_one(settings: Settings, entry: PendingApproval) -> None:
    """Fetches the page fresh (cheap -- one API call) to decide between
    three outcomes: nothing changed yet (leave pending), the page was
    edited again while waiting (flag for re-review, per the module
    docstring's "comment, don't silently revert status" rule -- a human is
    likely already looking at this ticket), or the configured approved
    status has been reached (resume implementation with the *saved* diff,
    never a freshly re-fetched one -- see run_pipeline's `resume` param).
    """
    deps = build_deps(settings)
    try:
        page = await deps.confluence.fetch_page(entry["page_id"])

        # Body content, not raw version, is what actually matters (a
        # metadata-only version bump shouldn't re-flag) -- checksum the same
        # way confluence/diff.py does, rather than trusting version alone.
        fresh_checksum = compute_checksum(_to_plain_text(page.body_html))
        if fresh_checksum != entry["page_body_checksum"] and not entry.get("stale_reason"):
            logger.info(
                "Page %s changed again while its story %s was awaiting approval; flagging for re-review.",
                entry["page_id"], entry["jira_issue_key"],
            )
            try:
                await deps.jira.add_comment(
                    entry["jira_issue_key"],
                    "The Confluence spec changed again while this story was awaiting approval "
                    f"(now v{page.version}). Please review the latest content before approving -- "
                    "this story's description reflects what it looked like when this story was "
                    "created, not the current page.",
                )
            except Exception as exc:
                logger.warning("Could not comment re-review notice on %s: %s", entry["jira_issue_key"], exc)
            stale_entry: PendingApproval = dict(entry)  # type: ignore[assignment]
            stale_entry["stale_reason"] = f"page edited again at v{page.version} while awaiting approval"
            deps.pending_approvals.put(stale_entry)
            return

        if entry.get("stale_reason"):
            # Already flagged -- don't keep re-checking approval status for
            # a story whose underlying content is known-stale; a human
            # needs to look at it (re-run "Plan a Sprint" or manually
            # retrigger) rather than this poller silently resuming on
            # possibly-outdated content once a status happens to match.
            return

        status = await deps.jira.get_issue_status(entry["jira_issue_key"])
        approved_name = settings.jira_approved_status_name.strip().lower()
        if not approved_name or status.status_name.strip().lower() != approved_name:
            return

        logger.info(
            "Story %s for page %s reached %r; resuming implementation.",
            entry["jira_issue_key"], entry["page_id"], status.status_name,
        )
        deps.pending_approvals.delete(entry["page_id"])
        await run_pipeline(entry["page_id"], deps=deps, resume=entry)
    except Exception:
        logger.exception("Approval check failed for page %s (%s)", entry["page_id"], entry.get("jira_issue_key"))
    finally:
        await deps.confluence.aclose()
        await deps.github.aclose()
        await deps.email_client.aclose()
        await deps.jira.aclose()


async def check_pending_approvals(settings: Settings) -> None:
    """One check cycle for one user -- every pending entry, sequentially
    (these are rare/low-volume compared to the general poller's page scan,
    so no per-user lock/concurrency-guard is worth the complexity here).
    """
    if not settings.jira_approval_required:
        return
    store_deps = build_deps(settings)
    try:
        entries = store_deps.pending_approvals.list_all()
    finally:
        await store_deps.confluence.aclose()
        await store_deps.github.aclose()
        await store_deps.email_client.aclose()
        await store_deps.jira.aclose()

    for entry in entries:
        await _check_one(settings, entry)


async def approval_poll_scan_loop() -> None:
    """Started once from webhook/app.py's lifespan alongside poll_scan_loop.
    Ticks independently of that loop's own interval -- approval checks are
    cheap (one Jira status fetch per pending entry) and don't need to be
    tied to CONFLUENCE_POLL_INTERVAL_SECONDS.
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
                    settings = get_settings(username)
                    if not settings.jira_approval_required:
                        continue
                    now = time.monotonic()
                    last = last_checked.get(username)
                    if last is not None and now - last < APPROVAL_POLL_TICK_SECONDS:
                        continue
                    last_checked[username] = now
                    asyncio.create_task(check_pending_approvals(settings))
        except Exception:
            logger.exception("Approval poll scan tick failed")
        await asyncio.sleep(APPROVAL_POLL_TICK_SECONDS)
