"""Per-user background polling for the Jira bug-watch dashboard."""

from __future__ import annotations

import logging
import time

from confluence_pr_agent.config import get_process_config, get_settings
from confluence_pr_agent.jira.bug_triage import poll_jira, _save_scan

logger = logging.getLogger(__name__)
JIRA_BUG_POLL_TICK_SECONDS = 30


def mark_approved_issues(settings, scan: dict) -> None:
    approved_name = settings.jira_approved_status_name.strip().lower()
    if not settings.jira_approval_required or not approved_name:
        return
    started = False
    for issue in scan.get("issues", []):
        if issue.get("status", "").strip().lower() != approved_name:
            continue
        if issue.get("triage") != "approved":
            issue["triage"] = "approved"
            started = True
            logger.info("Jira bug %s is approved and ready for agent execution", issue["key"])
    if started:
        _save_scan(settings, scan["issues"])


async def jira_bug_poll_scan_loop() -> None:
    last_polled: dict[str, float] = {}
    process = get_process_config()
    while True:
        try:
            for user_dir in process.users_dir_path.iterdir():
                if not user_dir.is_dir():
                    continue
                username = user_dir.name
                settings = get_settings(username)
                if not settings.jira_bug_poll_enabled:
                    continue
                now = time.monotonic()
                previous = last_polled.get(username)
                if previous is not None and now - previous < settings.jira_bug_poll_interval_seconds:
                    continue
                last_polled[username] = now
                try:
                    scan = await poll_jira(settings)
                    mark_approved_issues(settings, scan)
                except Exception:
                    logger.exception("Jira bug poll failed for user %s", username)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Jira bug poll scan failed")
        await asyncio.sleep(JIRA_BUG_POLL_TICK_SECONDS)