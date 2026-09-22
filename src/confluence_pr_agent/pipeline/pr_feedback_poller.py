"""Background poller that watches open PRs tagged with "agent:opened" for new
human review comments, re-runs the change engine with the reviewer's feedback
as retry_context, reruns the test/lint gate, and pushes follow-up commits.

Modeled structurally after approval_poller.py: a single scan loop started
from webhook/app.py's lifespan, scanning each provisioned user on their configured
pr_feedback_poll_interval_seconds timer.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from confluence_pr_agent.agent.prompts import build_single_repo_context
from confluence_pr_agent.confluence.diff import _to_plain_text, compute_checksum
from confluence_pr_agent.config import Settings, get_process_config, get_settings
from confluence_pr_agent.models import PageDiff, PageSnapshot, RepoTarget
from confluence_pr_agent.pipeline.orchestrator import LABEL_AGENT_OPENED, PipelineDeps, build_deps
from confluence_pr_agent.storage.page_store import StoredPage
from confluence_pr_agent.testing.test_runner import run_tests

logger = logging.getLogger(__name__)

PR_FEEDBACK_POLL_TICK_SECONDS = 30
BOT_SIGNATURE_PREFIX = "🤖 **confluence-pr-agent**"


async def _handle_comment_feedback(
    deps: PipelineDeps,
    settings: Settings,
    page: StoredPage,
    target_repo: str,
    rt: RepoTarget,
    pr_number: int,
    pr_branch: str,
    comment: dict,
    current_attempt: int,
    max_attempts: int,
) -> None:
    """Clones the repo branch, runs implement_change with feedback context,
    runs the test/lint gate, pushes follow-up if passing, and replies to the PR.
    """
    comment_id = comment.get("id")
    comment_body = str(comment.get("body") or "")
    feedback_context = comment_body.strip()

    repo_dir = settings.workdirs_path / f"feedback-{page['page_id']}-{pr_number}-{int(time.time())}"
    try:
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)

        logger.info(
            "Checking out %s branch %s for PR #%s feedback fix (attempt %s/%s)...",
            target_repo,
            pr_branch,
            pr_number,
            current_attempt,
            max_attempts,
        )
        await deps.git.clone(target_repo, repo_dir, rt.base_branch)
        await deps.git.checkout_existing_branch(repo_dir, pr_branch)

        page_snapshot = PageSnapshot(
            page_id=page["page_id"],
            title=page["title"],
            version=page["version"],
            body_html=page["body_html"],
            url=page.get("url", ""),
        )
        plain_text = _to_plain_text(page["body_html"])
        diff_text = (
            f"(PR review feedback on PR #{pr_number} in {target_repo})\n\n"
            f"Reviewer Comment:\n{feedback_context}\n\n"
            f"---\n\n"
            f"Original Spec ({page_snapshot.title} v{page_snapshot.version}):\n\n"
            f"{plain_text}"
        )
        diff = PageDiff(
            page=page_snapshot,
            previous_version=page["version"],
            diff_text=diff_text,
            is_first_seen=False,
            body_checksum=page.get("body_checksum") or compute_checksum(plain_text),
            repo_context=build_single_repo_context(rt),
        )
        if settings.coding_standards:
            diff.standards_text = settings.coding_standards

        change = await deps.change_engine.implement_change(
            repo_dir,
            diff,
            settings.change_agent_max_turns,
            retry_context=comment_body,
        )

        if not change.success:
            logger.warning(
                "Change engine failed during PR feedback fix for %s #%s: %s",
                target_repo,
                pr_number,
                change.summary,
            )
            reply = (
                f"{BOT_SIGNATURE_PREFIX} — fix attempt {current_attempt} of {max_attempts}\n\n"
                "I attempted to address your feedback, but the coding agent encountered an error:\n\n"
                f"> {change.summary}\n\n"
                "No changes were pushed. Please inspect manually."
            )
            await deps.github.add_pull_request_comment(target_repo, pr_number, reply)
            return

        # Rerun tests and lint gate
        test_result = await run_tests(repo_dir, rt.test_command)
        lint_result = None
        if test_result.passed and rt.lint_command:
            lint_result = await run_tests(repo_dir, rt.lint_command)

        tests_passed = test_result.passed and (lint_result is None or lint_result.crashed or lint_result.passed)

        if tests_passed:
            commit_message = (
                f"Address PR feedback (attempt {current_attempt} of {max_attempts})\n\n"
                f"Feedback: {feedback_context[:200]}\n\n"
                f"{change.summary}"
            )
            await deps.git.commit_all(repo_dir, commit_message)
            head_sha = await deps.git.head_sha(repo_dir)
            await deps.git.push(repo_dir, pr_branch)

            files_changed = await deps.git.changed_files(repo_dir) or change.files_changed
            files_list = "\n".join(f"- `{f}`" for f in files_changed) if files_changed else "_(no files listed)_"
            reply = (
                f"{BOT_SIGNATURE_PREFIX} — fix attempt {current_attempt} of {max_attempts}\n\n"
                f"Applied changes based on your feedback.\n\n"
                f"**Commit:** `{head_sha}`\n\n"
                f"**Files changed:**\n{files_list}\n\n"
                f"**Summary:** {change.summary}"
            )
            await deps.github.add_pull_request_comment(target_repo, pr_number, reply)
            logger.info(
                "Successfully pushed feedback commit %s to %s PR #%s (attempt %s/%s)",
                head_sha,
                target_repo,
                pr_number,
                current_attempt,
                max_attempts,
            )
        else:
            fail_output = test_result.output if not test_result.passed else (lint_result.output if lint_result else "")
            truncated_output = fail_output[-2500:] if fail_output else "Unknown test failure"
            reply = (
                f"{BOT_SIGNATURE_PREFIX} — fix attempt {current_attempt} of {max_attempts}\n\n"
                "I attempted to address your feedback, but the test/lint suite failed:\n\n"
                f"```\n{truncated_output}\n```\n\n"
                "The follow-up commit was NOT pushed. Please provide further guidance or fix manually."
            )
            await deps.github.add_pull_request_comment(target_repo, pr_number, reply)
            logger.warning(
                "Tests/lint failed for %s PR #%s on feedback attempt %s/%s; posted reply without pushing.",
                target_repo,
                pr_number,
                current_attempt,
                max_attempts,
            )

    finally:
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        if comment_id is not None:
            deps.store.update_repo_pr_field(page["page_id"], target_repo, "last_seen_comment_id", comment_id)
            deps.store.update_repo_pr_field(page["page_id"], target_repo, "feedback_attempts", current_attempt)


async def _check_page_prs(
    deps: PipelineDeps,
    settings: Settings,
    page: StoredPage,
    bot_login: str,
) -> None:
    """Inspects all open PRs associated with a stored page for new review comments."""
    repo_prs: dict = dict(page.get("repo_prs") or {})
    if not repo_prs and page.get("open_pr_number") and page.get("open_pr_branch"):
        repo_prs[settings.target_repo] = {
            "open_pr_number": page.get("open_pr_number"),
            "open_pr_branch": page.get("open_pr_branch"),
        }

    targets_by_name = {t.target_repo: t for t in settings.resolved_repo_targets}

    for target_repo, pr_info in repo_prs.items():
        pr_number = pr_info.get("open_pr_number")
        pr_branch = pr_info.get("open_pr_branch")
        if not pr_number or not pr_branch:
            continue

        try:
            pr_status = await deps.github.get_pull_request(target_repo, pr_number)
        except Exception as exc:
            logger.warning("Failed to get PR #%s status for %s: %s", pr_number, target_repo, exc)
            continue

        if not pr_status.is_open:
            continue

        if LABEL_AGENT_OPENED not in pr_status.labels:
            continue

        rt = targets_by_name.get(target_repo)
        if rt is None:
            rt = RepoTarget(
                target_repo=target_repo,
                base_branch=settings.target_repo_base_branch,
                test_command=settings.target_repo_test_command,
                lint_command=settings.target_repo_lint_command,
            )

        try:
            comments = await deps.github.list_review_comments(target_repo, pr_number)
        except Exception as exc:
            logger.warning("Failed to fetch review comments for %s #%s: %s", target_repo, pr_number, exc)
            continue

        last_seen_id = pr_info.get("last_seen_comment_id", 0) or 0
        new_human_comments = []
        for c in comments:
            cid = c.get("id", 0)
            if cid <= last_seen_id:
                continue
            author = (c.get("user") or {}).get("login", "")
            if bot_login and author.lower() == bot_login.lower():
                continue
            body = str(c.get("body") or "").strip()
            if not body:
                continue
            if body.startswith(BOT_SIGNATURE_PREFIX):
                continue
            new_human_comments.append(c)

        if not new_human_comments:
            continue

        # Sort chronologically by comment ID
        new_human_comments.sort(key=lambda c: c.get("id", 0))
        target_comment = new_human_comments[0]

        feedback_attempts = pr_info.get("feedback_attempts", 0) or 0
        max_attempts = max(1, settings.change_agent_max_attempts)

        if feedback_attempts >= max_attempts:
            logger.info(
                "PR #%s on %s has already exhausted max feedback attempts (%s/%s)",
                pr_number,
                target_repo,
                feedback_attempts,
                max_attempts,
            )
            reply = (
                f"{BOT_SIGNATURE_PREFIX} — fix attempt {feedback_attempts} of {max_attempts}\n\n"
                f"Maximum feedback fix attempts ({max_attempts}) reached for this PR. "
                "Please inspect and apply any remaining changes manually."
            )
            try:
                await deps.github.add_pull_request_comment(target_repo, pr_number, reply)
            except Exception as exc:
                logger.warning("Failed to post max-attempts reply to %s #%s: %s", target_repo, pr_number, exc)
            deps.store.update_repo_pr_field(page["page_id"], target_repo, "last_seen_comment_id", target_comment.get("id"))
            continue

        current_attempt = feedback_attempts + 1
        try:
            await _handle_comment_feedback(
                deps=deps,
                settings=settings,
                page=page,
                target_repo=target_repo,
                rt=rt,
                pr_number=pr_number,
                pr_branch=pr_branch,
                comment=target_comment,
                current_attempt=current_attempt,
                max_attempts=max_attempts,
            )
        except Exception as exc:
            logger.exception("Error handling PR feedback for %s #%s: %s", target_repo, pr_number, exc)


async def check_pr_feedback(settings: Settings) -> None:
    """One check cycle for one user -- iterates over open PRs in PageStore."""
    if not settings.pr_feedback_poll_enabled:
        return

    deps = build_deps(settings)
    try:
        bot_login = ""
        try:
            bot_login = await deps.github.test_connection()
        except Exception as exc:
            logger.warning("Could not identify GitHub bot login: %s", exc)

        pages = deps.store.list_all()
        for page in pages:
            await _check_page_prs(deps, settings, page, bot_login)
    except Exception:
        logger.exception("PR feedback poll cycle failed")
    finally:
        await deps.confluence.aclose()
        await deps.github.aclose()
        await deps.email_client.aclose()
        await deps.jira.aclose()


async def pr_feedback_poll_scan_loop() -> None:
    """Process-wide background scan loop started from webhook/app.py lifespan."""
    last_polled: dict[str, float] = {}
    process = get_process_config()

    while True:
        try:
            if process.users_dir_path.exists():
                for user_dir in process.users_dir_path.iterdir():
                    if not user_dir.is_dir():
                        continue
                    username = user_dir.name
                    settings = get_settings(username)
                    if not settings.pr_feedback_poll_enabled:
                        continue
                    now = time.monotonic()
                    last = last_polled.get(username)
                    interval = max(10, settings.pr_feedback_poll_interval_seconds)
                    if last is not None and now - last < interval:
                        continue
                    last_polled[username] = now
                    asyncio.create_task(check_pr_feedback(settings))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("PR feedback poll scan tick failed")
        await asyncio.sleep(PR_FEEDBACK_POLL_TICK_SECONDS)
