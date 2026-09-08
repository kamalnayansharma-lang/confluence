"""Orchestrates the full pipeline:

Confluence page changed -> diff vs. last-seen version -> clone target repo ->
a pluggable change engine implements the change + tests -> run tests locally
(gate, with a bounded self-correction retry loop) -> an LLM judge reviews
the diff (never blocks -- only changes what kind of PR opens) -> commit/push
/open PR -> email the team.

Which change engine actually writes the code (Claude Code, Cursor, GitHub
Copilot, ...) is a config choice (CHANGE_AGENT_ENGINE), not something this
module knows about -- see agent/base.py and agent/factory.py. Same for which
model reviews it (JUDGE_PROVIDER) -- see judge/base.py and judge/factory.py.

The only two things that stop a PR from opening at all are: the change
engine failing outright / making no changes, and the test suite still
failing after every retry attempt. The LLM judge is advisory, not a gate --
a "rejected" verdict still opens a PR, just as a draft flagged "[Needs
Work]" with its rubric attached, instead of silently doing nothing. This is
deliberate: the pipeline runs unattended (webhook-triggered), so there is no
one present to act on a pipeline-internal "are you sure?" -- the one place a
human reviews this system's output is at PR merge time, which this design
preserves and feeds with more information instead.

The page-version store is only updated once a PR has actually opened
(including a judge-rejected draft), so a run that produced no durable
artifact at all (agent failure, tests still failing after retries, PR API
error) is retried from the same diff on the next webhook delivery / manual
re-trigger rather than being silently marked as processed.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from confluence_pr_agent.agent.base import ChangeEngine
from confluence_pr_agent.agent.factory import build_change_engine
from confluence_pr_agent.agent.prompts import build_repo_context, build_single_repo_context
from confluence_pr_agent.config import Settings, get_process_config, get_settings
from confluence_pr_agent.confluence.client import ConfluenceClient
from confluence_pr_agent.confluence.diff import _to_plain_text, compute_diff
from confluence_pr_agent.confluence.related_context import gather_related_context
from confluence_pr_agent.jira.client import JiraClient
from confluence_pr_agent.jira.story_writer import generate_story_content
from confluence_pr_agent.judge.base import ChangeJudge
from confluence_pr_agent.judge.factory import build_judge, judge_configured
from confluence_pr_agent.models import (
    ChangeAgentResult,
    JiraIssueResult,
    JudgeResult,
    PageDiff,
    PageSnapshot,
    PipelineResult,
    RepoChangeResult,
    RepoTarget,
    RepoTestResult,
    RunRecord,
)
from confluence_pr_agent.notifications.base import EmailClient
from confluence_pr_agent.notifications.postmark_client import PostmarkClient
from confluence_pr_agent.notifications.sendgrid_client import SendGridClient
from confluence_pr_agent.notifications.templates import build_summary_email
from confluence_pr_agent.pipeline.stages import STAGE_KEYS
from confluence_pr_agent.repo.git_client import GitClient
from confluence_pr_agent.repo.github_client import GitHubClient
from confluence_pr_agent.storage.page_store import PageStore, StoredPage
from confluence_pr_agent.storage.pending_approval_store import PendingApproval, PendingApprovalStore
from confluence_pr_agent.storage.run_store import RunStore
from confluence_pr_agent.storage.sprint_plan_store import SprintPlanStore
from confluence_pr_agent.testing.test_runner import run_tests

logger = logging.getLogger(__name__)

_ASSESSMENT_ICON = {"pass": "✅", "warning": "⚠️", "fail": "❌"}

# Applied to the PR based on the judge's verdict so a rejected/warned run is
# queryable via the GitHub API/CLI, not just by title-string matching. Only
# ever two labels exist between them -- a clean "approved" run means neither
# is present.
LABEL_NEEDS_WORK = "agent:needs-work"
LABEL_WARNING = "agent:warning"
_LABEL_COLOR = {LABEL_NEEDS_WORK: "d73a4a", LABEL_WARNING: "fbca04"}
_LABEL_DESCRIPTION = {
    LABEL_NEEDS_WORK: "Opened by confluence-pr-agent -- the LLM judge found at least one criterion clearly unmet.",
    LABEL_WARNING: "Opened by confluence-pr-agent -- the LLM judge approved with at least one non-blocking concern.",
}


async def _sync_verdict_label(github: GitHubClient, owner_repo: str, pr_number: int, verdict: str) -> None:
    """Best-effort and isolated, like the email step: labeling is an
    annotation on top of an already-successful PR, not something that
    should fail the run (e.g. a fine-grained PAT without the "issues" scope
    would 403 on this and must not turn an opened PR into a reported
    failure). Always resyncs both labels, not just the target one, so
    reusing an existing PR (see run_pipeline) correctly clears a stale
    label from a previous run's different verdict.
    """
    target = LABEL_NEEDS_WORK if verdict == "rejected" else LABEL_WARNING if verdict == "approved_with_warnings" else None
    try:
        if target:
            await github.ensure_label(owner_repo, target, _LABEL_COLOR[target], _LABEL_DESCRIPTION[target])
            await github.add_labels(owner_repo, pr_number, [target])
        for other in (LABEL_NEEDS_WORK, LABEL_WARNING):
            if other != target:
                await github.remove_label(owner_repo, pr_number, other)
    except Exception as exc:
        logger.warning("Failed to sync judge labels on PR #%s: %s", pr_number, exc)


def _build_pr_body(
    page: PageSnapshot, diff: PageDiff, change: ChangeAgentResult, judge_result: JudgeResult | None
) -> str:
    """PR description: source/summary/files as before, plus a rubric table
    of the judge's per-criterion review when one ran -- this is the one
    place a human actually looks, so the review needs to live here, not
    just on the internal /ui/runs/{id} page.
    """
    parts = [
        f"**Source:** [{page.title}]({page.url}) (v{diff.previous_version} -> v{page.version})\n",
        f"**Summary of change:**\n{change.summary}\n",
    ]

    if judge_result and judge_result.verdict == "rejected":
        parts.append(
            "> ⚠️ **Needs review before merge.** The LLM judge below found at least one "
            "criterion clearly unmet. This PR still opened automatically (as a draft) so nothing is "
            "lost -- fix in place on this branch, or close it, at your discretion.\n"
        )

    if judge_result and judge_result.criteria:
        rows = "\n".join(
            f"| {c.label} | {_ASSESSMENT_ICON.get(c.assessment, c.assessment)} {c.assessment} | {c.note} |"
            for c in judge_result.criteria
        )
        parts.append(
            "**LLM Judge review:**\n\n"
            "| Criterion | Assessment | Note |\n"
            "|---|---|---|\n"
            f"{rows}\n\n"
            f"{judge_result.reasoning}\n"
        )
        if judge_result.concerns:
            concerns = "\n".join(f"- {c}" for c in judge_result.concerns)
            parts.append(f"**Concerns:**\n{concerns}\n")
    elif judge_result and judge_result.verdict == "skipped":
        parts.append(f"_LLM Judge review: skipped ({judge_result.reasoning})_\n")

    parts.append("**Files changed:**\n" + "\n".join(f"- `{f}`" for f in change.files_changed) + "\n")
    parts.append("_Opened automatically by confluence-pr-agent._")
    return "\n".join(parts)


def _repo_slug(target_repo: str) -> str:
    """owner/name -> a filesystem-safe subdirectory name, for the multi-repo
    workspace layout (single-repo runs never nest, so never call this).
    """
    return target_repo.replace("/", "_")


def _repo_pr_info(previous_page: dict | None, target_repo: str) -> tuple[int | None, str | None]:
    """(open_pr_number, open_pr_branch) for `target_repo` from the stored
    page record, if any -- checks the per-repo `repo_prs` map first (see
    storage/page_store.py::StoredPage), falling back to the legacy singular
    open_pr_number/open_pr_branch fields when there's no repo_prs map at all
    yet (a record from before multi-repo support, or one that's only ever
    been single-repo). A record that DOES have a repo_prs map but nothing
    for this specific repo genuinely has no open PR there -- doesn't fall
    back to the legacy fields in that case, which would misattribute an
    unrelated repo's stale PR.
    """
    if not previous_page:
        return None, None
    repo_prs = previous_page.get("repo_prs") or {}
    entry = repo_prs.get(target_repo)
    if entry:
        return entry.get("open_pr_number"), entry.get("open_pr_branch")
    if not repo_prs:
        return previous_page.get("open_pr_number"), previous_page.get("open_pr_branch")
    return None, None


# Aggregate status for a multi-repo run's overall RunRecord.status: the
# worst outcome across all repo_results wins, so a partial failure never
# reads identically to full success in /ui/runs. Repos that simply had
# nothing to do ("no_changes") don't count as a failure on their own.
_STATUS_SEVERITY = {"error": 4, "tests_failed": 3, "judge_rejected": 2, "opened_pr": 1, "no_changes": 0}


def _aggregate_status(repo_results: list[RepoChangeResult]) -> str:
    if not repo_results:
        return "error"
    worst = max(repo_results, key=lambda r: _STATUS_SEVERITY.get(r.status, 0))
    if worst.status == "no_changes" and len(repo_results) > 1:
        # Every in-scope repo had nothing to do -- distinct from a single-
        # repo "agent made no file changes" error (still an error there,
        # since there's nowhere else the change could have landed); in
        # multi-repo it's a legitimate (if unusual) outcome; surfaced as
        # its own status rather than silently looking like "opened_pr".
        return "no_changes"
    return worst.status


@dataclass
class PipelineDeps:
    """Injected collaborators -- lets tests substitute fakes for each stage."""

    settings: Settings
    confluence: ConfluenceClient
    store: PageStore
    git: GitClient
    github: GitHubClient
    email_client: EmailClient
    change_engine: ChangeEngine
    run_store: RunStore
    judge: ChangeJudge
    jira: JiraClient
    pending_approvals: PendingApprovalStore


def _build_email_client(settings: Settings) -> EmailClient:
    provider = settings.email_provider.strip().lower()
    if provider == "postmark":
        return PostmarkClient(settings.postmark_api_key)
    return SendGridClient(settings.sendgrid_api_key)  # default -- also covers an unrecognized value


def build_deps(settings: Settings | None = None) -> PipelineDeps:
    # No settings given -- resolves to the single bootstrap/default user
    # (config.py's ProcessConfig.resolved_default_user), not a bare global
    # get_settings() (multi-tenant: there's no such thing anymore, every
    # user has their own). This is what /webhook/confluence relies on (see
    # webhook/app.py) -- it has no way to know which of several users a
    # webhook delivery is "for", so it always resolves this one fixed
    # identity, same as it always has.
    settings = settings or get_settings(get_process_config().resolved_default_user)
    if settings.repo_provider != "github":
        # Fail loudly here rather than silently building a GitClient/
        # GitHubClient anyway -- REPO_PROVIDER is config surface for future
        # providers (see config.py), not something this pipeline can
        # actually act on yet. Better than quietly doing GitHub work against
        # a setting that says otherwise.
        raise ValueError(
            f"REPO_PROVIDER={settings.repo_provider!r} is not supported yet -- only 'github' is implemented."
        )
    return PipelineDeps(
        settings=settings,
        confluence=ConfluenceClient(
            base_url=settings.confluence_base_url,
            email=settings.confluence_user_email,
            api_token=settings.confluence_api_token,
        ),
        store=PageStore(settings.page_store_path),
        git=GitClient(settings.github_token),
        github=GitHubClient(settings.github_token),
        email_client=_build_email_client(settings),
        change_engine=build_change_engine(settings),
        run_store=RunStore(settings.runs_store_path),
        judge=build_judge(settings),
        jira=JiraClient(
            base_url=settings.jira_base_url,
            email=settings.jira_user_email,
            api_token=settings.jira_api_token,
        ),
        pending_approvals=PendingApprovalStore(settings.pending_approvals_store_path),
    )


async def _setup_repo_branch(
    deps: PipelineDeps,
    rt: RepoTarget,
    repo_dir: Path,
    page_id: str,
    page: PageSnapshot,
    previous_page: dict | None,
) -> tuple[str, int | None, bool]:
    """Clones `rt` and checks out either a still-open PR's branch (reuse) or
    a fresh one -- everything that must happen before the shared change-
    engine call edits the workspace. Returns (branch_name, reuse_pr_number,
    reused). Mirrors the single-repo version of this logic exactly, just
    parametrized per repo instead of off settings.target_repo directly.
    """
    await deps.git.clone(rt.target_repo, repo_dir, rt.base_branch)

    reuse_pr_number, reuse_branch = _repo_pr_info(previous_page, rt.target_repo)
    if reuse_branch:
        try:
            pr_status = await deps.github.get_pull_request(rt.target_repo, reuse_pr_number)
            if not pr_status.is_open:
                reuse_pr_number, reuse_branch = None, None
        except Exception as exc:
            logger.warning(
                "Could not check status of previously-opened PR #%s for %s on page %s; opening a new PR instead: %s",
                reuse_pr_number, rt.target_repo, page_id, exc,
            )
            reuse_pr_number, reuse_branch = None, None

    if reuse_branch:
        try:
            await deps.git.checkout_existing_branch(repo_dir, reuse_branch)
            logger.info(
                "PR #%s for %s on page %s is still open; reusing its branch instead of opening a new PR.",
                reuse_pr_number, rt.target_repo, page_id,
            )
            return reuse_branch, reuse_pr_number, True
        except Exception as exc:
            logger.warning(
                "Could not check out existing PR branch %s for %s on page %s; opening a new PR instead: %s",
                reuse_branch, rt.target_repo, page_id, exc,
            )

    branch_name = f"confluence-sync/page-{page_id}-v{page.version}-{int(time.time())}"
    await deps.git.create_branch(repo_dir, branch_name)
    return branch_name, None, False


async def _finalize_repo(
    deps: PipelineDeps,
    rt: RepoTarget,
    repo_dir: Path,
    branch_name: str,
    reuse_pr_number: int | None,
    reused: bool,
    page: PageSnapshot,
    diff: PageDiff,
    change: ChangeAgentResult,
    judge_enabled: bool,
) -> RepoChangeResult:
    """Commits/pushes/opens-or-updates a PR for one in-scope, actually-
    touched repo, once the shared agent call + test retry loop
    (run_pipeline) has already succeeded across the whole workspace.
    Independent per repo on purpose: a failure here (PR API error, judge
    call failing) only affects this repo's own RepoChangeResult and never
    aborts the others -- see _aggregate_status for how a partial failure is
    reflected in the overall run's status.
    """
    files_changed = await deps.git.changed_files(repo_dir)

    judge_result: JudgeResult | None = None
    if judge_enabled:
        if not judge_configured(deps.settings):
            judge_result = JudgeResult(
                verdict="skipped",
                reasoning=(
                    f"No API key configured for judge provider '{deps.settings.judge_provider}'; "
                    "skipping LLM review."
                ),
            )
        else:
            try:
                code_diff = await deps.git.diff(repo_dir)
                judge_result = await deps.judge.evaluate(diff, change, code_diff)
            except Exception as exc:
                logger.warning(
                    "LLM judge failed for %s; proceeding without review: %s", rt.target_repo, exc
                )
                judge_result = JudgeResult(
                    verdict="skipped", reasoning=f"Judge call failed; proceeding without review: {exc}"
                )
        if judge_result.verdict == "rejected":
            logger.warning(
                "LLM judge rejected the change to %s: %s -- opening a flagged draft PR anyway.",
                rt.target_repo, judge_result.reasoning,
            )

    is_rejected = judge_result is not None and judge_result.verdict == "rejected"

    try:
        commit_message = f"Sync with Confluence spec: {page.title} (v{page.version})\n\n{change.summary}"
        await deps.git.commit_all(repo_dir, commit_message)

        pr_title = f"Sync with Confluence: {page.title} (v{page.version})"
        if is_rejected:
            pr_title = f"[Needs Work] {pr_title}"
        pr_body = _build_pr_body(page, diff, change, judge_result)
        verdict_for_labels = judge_result.verdict if judge_result else "approved"

        await deps.git.push(repo_dir, branch_name)
        if reused:
            pr_body = (
                f"_Updated automatically: the spec changed again (v{diff.previous_version} -> "
                f"v{page.version}) while this PR was still open, so it was updated in place "
                "instead of opening a new one._\n\n"
            ) + pr_body
            pull_request = await deps.github.update_pull_request(
                owner_repo=rt.target_repo, pr_number=reuse_pr_number, title=pr_title, body=pr_body,
            )
        else:
            pull_request = await deps.github.open_pull_request(
                owner_repo=rt.target_repo,
                head_branch=branch_name,
                base_branch=rt.base_branch,
                title=pr_title,
                body=pr_body,
                draft=is_rejected,
            )

        await _sync_verdict_label(deps.github, rt.target_repo, pull_request.number, verdict_for_labels)

        return RepoChangeResult(
            target_repo=rt.target_repo,
            status="judge_rejected" if is_rejected else "opened_pr",
            files_changed=files_changed,
            pull_request=pull_request,
            reused_pr=reused,
            judge=judge_result,
        )
    except Exception as exc:
        # Unlike the reuse lookup in _setup_repo_branch, this can't fall
        # back to "just open a new PR instead" once reused -- the agent's
        # new commits may already be sitting on the existing branch.
        # Surfaced as this repo's own error rather than aborting the whole
        # run -- see _aggregate_status.
        logger.exception("Failed to open/update a PR for %s on page %s", rt.target_repo, page.page_id)
        error = f"failed to update existing PR #{reuse_pr_number}: {exc}" if reused else f"failed to open a PR: {exc}"
        return RepoChangeResult(
            target_repo=rt.target_repo, status="error", files_changed=files_changed,
            judge=judge_result, error=error,
        )


def _find_sprint_plan_jira_issue_key(settings: Settings, page_id: str) -> str | None:
    """A page's Jira story may have been created by Sprint Planning
    (ui/plan_sprint.py -> jira/story_writer.py) rather than by this
    pipeline's own reuse tracking below -- SprintPlanStore, not PageStore,
    is the source of truth for that. Without this, a page that only ever
    went through Sprint Planning has no PageStore record at all, so a
    later poll/webhook/retrigger run for it has no way to know a story
    already exists and opens a duplicate. Confirmed live: retriggering
    page 5898241 (story KAN-33, created via Sprint Planning) created a
    second story, KAN-55, instead of finding KAN-33. Cheap JSON-file scan
    -- same pattern sprint_runner.py itself uses -- so this doesn't need
    its own PipelineDeps field.
    """
    plan_store = SprintPlanStore(settings.sprint_plan_store_path)
    for plan in plan_store.list_all():
        for page in plan["pages"]:
            if page["page_id"] == page_id and page.get("jira_issue_key"):
                return page["jira_issue_key"]
    return None


async def run_pipeline(
    page_id: str,
    deps: PipelineDeps | None = None,
    force: bool = False,
    resume: PendingApproval | None = None,
) -> PipelineResult:
    """`resume`, when set (only ever passed by pipeline/approval_poller.py),
    skips fetch/label-gate/diff-computation/story-creation entirely and
    jumps straight to repo-routing + implementation using the exact
    already-approved diff/story recorded in `resume` -- see
    storage/pending_approval_store.py. This is what JIRA_APPROVAL_REQUIRED's
    "implement only what was actually reviewed" guarantee rests on: a
    resumed run never re-fetches or re-diffs the live page, so a spec edit
    that happened after approval can't silently sneak into what gets
    implemented (approval_poller.py detects that case separately and flags
    it for re-review instead of resuming).
    """
    owns_deps = deps is None
    deps = deps or build_deps()
    settings = deps.settings

    run_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(timezone.utc)
    start_clock = time.monotonic()

    # A display string for RunRecord.target_repo (still a single field, kept
    # for today's single-repo-shaped UI -- see models.py::RunRecord's
    # repo_results docstring). Computed once, from every *configured* repo
    # regardless of which ones actually end up in scope for this page, so
    # the "running" placeholder below has something sensible to show before
    # scope is even resolved; finish() overrides it with the real in-scope
    # set once known.
    all_configured_repos = ", ".join(rt.target_repo for rt in settings.resolved_repo_targets)

    # Declared here (not first assigned deep inside the try block) so
    # they're always safely referenceable in finish() and the outermost
    # except handler, even for a run that fails before Jira tracking would
    # otherwise be reached.
    jira_issue: JiraIssueResult | None = None
    jira_reused = False

    # Mutated as the pipeline progresses so finish() (and mark_stage itself)
    # can always report the latest known page/stage, even on a path that
    # fails before `page` would otherwise be assigned.
    progress: dict = {"stage": STAGE_KEYS[0], "page": None}

    def mark_stage(stage: str) -> None:
        """Records the stage the pipeline is currently entering, so a run in
        progress shows up in /ui/runs as status="running" with a live stage
        (not just invisible until it finishes -- which, for a real agentic
        engine, can take minutes) and so a finished run's detail page can
        show exactly how far it got. If the process dies mid-run this is
        left behind rather than ever reaching a terminal status; the delete
        button in /ui/runs is the way to clean one of those up.
        """
        assert stage in STAGE_KEYS, f"unknown stage {stage!r}"
        progress["stage"] = stage
        page = progress["page"]
        deps.run_store.upsert_run(
            RunRecord(
                run_id=run_id,
                started_at=started_at.isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=time.monotonic() - start_clock,
                page_id=page_id,
                page_title=page.title if page else "",
                confluence_url=page.url if page else "",
                engine=settings.change_agent_engine,
                target_repo=all_configured_repos,
                status="running",
                current_stage=stage,
                max_attempts=max(1, settings.change_agent_max_attempts),
                # upsert_run REPLACES the whole record each call (see its
                # own docstring), so anything known so far has to be
                # re-included here every time or it gets wiped -- jira_issue
                # is set partway through the "create_jira_story" stage
                # (below), so the very next mark_stage() after that is the
                # first one that can carry it. Without this, the run detail
                # page's live "Progress" view showed a blank Jira Story step
                # for the whole rest of a still-running run, only filling in
                # once the run finished and finish() wrote the final record.
                jira_issue_key=jira_issue.key if jira_issue else None,
                jira_issue_url=jira_issue.url if jira_issue else None,
                jira_reused=jira_reused,
            )
        )

    mark_stage(STAGE_KEYS[0])  # "fetch_page"

    async def _sync_jira_on_finish(result: PipelineResult) -> None:
        """Best-effort and isolated, same as everything else that touches an
        external API here: a Jira outage must never turn an otherwise
        successful (or already-failed) run into something worse. Comments
        on the tracked issue with either why the run failed (so a developer
        can pick it up manually, per the "create the story regardless"
        design) or every PR that resulted -- one story covers the whole
        (possibly multi-repo) change, so its comment lists all of them, not
        just one.
        """
        if not result.jira_issue:
            return
        try:
            opened = [r for r in result.repo_results if r.pull_request]
            if opened:
                lines = []
                for r in opened:
                    line = f"Pull request opened for `{r.target_repo}`: {r.pull_request.url}"
                    if r.status == "judge_rejected":
                        line += " (opened as a draft -- the LLM judge found at least one criterion clearly unmet)"
                    elif r.judge and r.judge.verdict == "approved_with_warnings":
                        line += " (LLM judge flagged non-blocking concerns -- worth a look before merging)"
                    lines.append(line)
                failed = [r for r in result.repo_results if r.status == "error"]
                if failed:
                    lines.append("")
                    lines.extend(f"Failed for `{r.target_repo}`: {r.error}" for r in failed)
                await deps.jira.add_comment(result.jira_issue.key, "\n".join(lines))
            elif result.status in ("tests_failed", "error", "no_repo_matched"):
                reason = result.error or "See the run details."
                if result.status == "no_repo_matched":
                    reason = "None of the configured repos' routing labels matched this page."
                await deps.jira.add_comment(
                    result.jira_issue.key,
                    f"This change could not be completed automatically:\n\n{reason}\n\n"
                    "A developer may need to pick this up manually.",
                )
        except Exception as exc:
            logger.warning("Failed to sync Jira story %s: %s", result.jira_issue.key, exc)

    async def finish(result: PipelineResult) -> PipelineResult:
        """Records this run's terminal outcome before returning it."""
        page = result.page
        diff = result.diff
        change = result.change
        pull_request = result.pull_request
        tests = result.tests
        judge = result.judge
        await _sync_jira_on_finish(result)
        deps.run_store.upsert_run(
            RunRecord(
                run_id=run_id,
                started_at=started_at.isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=time.monotonic() - start_clock,
                page_id=page_id,
                page_title=page.title if page else "",
                confluence_url=page.url if page else "",
                engine=settings.change_agent_engine,
                target_repo=(
                    ", ".join(r.target_repo for r in result.repo_results) if result.repo_results else all_configured_repos
                ),
                status=result.status,
                current_stage=progress["stage"],
                files_changed=change.files_changed if change else [],
                pr_number=pull_request.number if pull_request else None,
                pr_url=pull_request.url if pull_request else None,
                pr_draft=pull_request.draft if pull_request else False,
                reused_pr=result.reused_pr,
                summary=change.summary if change else None,
                error=result.error,
                email_sent=result.email_sent,
                email_error=result.email_error,
                usage=change.usage if change else None,
                raw_log=(change.raw_log[-8000:] if change and change.raw_log else None),
                test_output=(tests.output[-8000:] if tests and tests.output else None),
                spec_diff=(diff.diff_text[:8000] if diff and diff.diff_text else None),
                judge_verdict=judge.verdict if judge else None,
                judge_reasoning=judge.reasoning if judge else None,
                judge_concerns=judge.concerns if judge else [],
                judge_criteria=judge.criteria if judge else [],
                attempts=result.attempts,
                max_attempts=max(1, settings.change_agent_max_attempts),
                jira_issue_key=result.jira_issue.key if result.jira_issue else None,
                jira_issue_url=result.jira_issue.url if result.jira_issue else None,
                jira_reused=result.jira_reused,
                repo_results=result.repo_results,
                # Structural, not re-derived per template render -- see
                # RunRecord.flagged_scope_gap's docstring for why this is
                # exactly the heading the out-of-scope prompt section asks
                # the agent to use, not a guess at its wording.
                flagged_scope_gap=bool(change and change.summary and "next steps" in change.summary.lower()),
            )
        )
        return result

    async def _fetch_diff_and_story() -> tuple[PageSnapshot, PageDiff, dict | None, JiraIssueResult | None, bool, bool] | PipelineResult:
        """Fetch -> label-gate -> diff -> Jira story creation/reuse, exactly
        today's prologue -- extracted verbatim (via `nonlocal jira_issue,
        jira_reused` below, so every line of the original body is
        unchanged) so run_pipeline can skip this entirely on a resumed run
        -- see the `resume` param's docstring above. Reuses the SAME
        mark_stage/finish closures either way, since this is still nested
        inside run_pipeline.

        Returns a terminal PipelineResult for the two early-exit cases
        (ignored / no_change_detected) -- the caller `await finish(...)`s
        it, same as every other terminal result in this function -- or the
        happy-path tuple otherwise.
        """
        nonlocal jira_issue, jira_reused
        page = await deps.confluence.fetch_page(page_id)
        progress["page"] = page

        allowed_labels = settings.confluence_allowed_labels_list
        if allowed_labels:
            page_labels = {label.lower() for label in page.labels}
            if not page_labels & set(allowed_labels):
                logger.info(
                    "Page %s labels %s don't include any of %s; ignoring.",
                    page_id,
                    sorted(page_labels),
                    allowed_labels,
                )
                return PipelineResult(
                    status="ignored",
                    page=page,
                    error=f"Page has none of the required labels: {', '.join(allowed_labels)}",
                )

        diff = compute_diff(deps.store, page, force=force)

        if not diff.is_first_seen and diff.diff_text == "":
            if diff.content_unchanged:
                logger.info(
                    "Page %s version bumped (v%s -> v%s) but content checksum matched; skipping.",
                    page_id,
                    diff.previous_version,
                    page.version,
                )
            else:
                logger.info("No version change for page %s (still v%s); skipping.", page_id, page.version)
            return PipelineResult(status="no_change_detected", page=page, diff=diff)

        logger.info(
            "Detected change on page %s: v%s -> v%s", page_id, diff.previous_version, page.version
        )

        previous_page = deps.store.get(page_id)

        # Enrich the diff once, up front, so both the Jira story (below) and
        # the coding agent's own prompt (much later) see the same context --
        # see models.py's PageDiff.related_context/standards_text docstrings.
        # Both are best-effort: neither can fail this run, only degrade to
        # None (confluence/related_context.py handles its own try/except;
        # the standards-page fetch below is wrapped the same way).
        if settings.standards_confluence_page_id.strip():
            try:
                standards_page = await deps.confluence.fetch_page(settings.standards_confluence_page_id.strip())
                diff.standards_text = _to_plain_text(standards_page.body_html)
            except Exception as exc:
                logger.warning("Could not fetch standards page %s: %s", settings.standards_confluence_page_id, exc)
        diff.related_context = await gather_related_context(deps.confluence, settings.confluence_space_key, page)

        # Create the Jira story before any code is touched (the story is
        # about the spec change, not the eventual implementation -- see
        # jira/story_writer.py), so a subsequent failure anywhere downstream
        # still has a durable place to record what went wrong (see
        # _sync_jira_on_finish). If a story from a previous still-open run
        # for this page is still open, update it instead of creating a
        # duplicate -- mirrors the PR-branch reuse check below.
        #
        # Every step below is independently try/excepted rather than one
        # broad try around the whole block on purpose: a real duplicate-story
        # bug used to live here -- a *follow-up* call (posting the diff
        # comment, or the AI-suggested-complexity comment) throwing was
        # caught by one shared except that reset `jira_issue = None`,
        # discarding the reference to the story that had just been
        # successfully created (or found) moments earlier. The next run then
        # had no memory of it and created a fresh duplicate. Each step here
        # can fail on its own -- logged, fails open -- without erasing
        # jira_issue once it's been set.
        mark_stage("create_jira_story")
        # True when a REUSED story (see below) is already sitting at
        # settings.jira_approved_status_name -- e.g. planned + confirmed +
        # approved through "Plan a Sprint" before this page's regular
        # pipeline ever ran. Without this, the JIRA_APPROVAL_REQUIRED gate
        # further down had no way to know the story it just found was
        # already approved, and would park it in a brand-new
        # PendingApproval anyway -- which nothing would ever resume, since
        # approval_poller.py only watches entries THIS pipeline itself
        # created (see that module's docstring). Confirmed live: an
        # already-approved sprint-planning story re-triggered via poll sat
        # in awaiting_approval indefinitely instead of implementing.
        already_approved = False
        if settings.jira_enabled and settings.jira_base_url and settings.jira_project_key:
            existing_key = previous_page.get("jira_issue_key") if previous_page else None
            if not existing_key:
                existing_key = _find_sprint_plan_jira_issue_key(settings, page_id)
            if existing_key:
                try:
                    existing_status = await deps.jira.get_issue_status(existing_key)
                    if existing_status.is_open:
                        jira_issue = JiraIssueResult(
                            key=existing_status.key,
                            url=f"{settings.jira_base_url.rstrip('/')}/browse/{existing_status.key}",
                        )
                        jira_reused = True
                        approved_name = settings.jira_approved_status_name.strip().lower()
                        if approved_name and existing_status.status_name.strip().lower() == approved_name:
                            already_approved = True
                except Exception as exc:
                    logger.warning(
                        "Could not check status of existing Jira story %s for page %s; "
                        "creating a new one instead: %s",
                        existing_key, page_id, exc,
                    )

            # Generated either way: refreshes a reused story's description,
            # or seeds a brand-new one's.
            story = await generate_story_content(settings, diff)

            if jira_issue is not None:
                # Reuse path -- keep the story's description in sync with
                # the latest spec state (previously left stale from whenever
                # it was first created) and leave a visible trail of what
                # changed this time, same as a brand-new story gets below.
                try:
                    await deps.jira.update_description(jira_issue.key, story.description, story.acceptance_criteria)
                except Exception as exc:
                    logger.warning("Failed to refresh description for Jira story %s: %s", jira_issue.key, exc)
                try:
                    diff_comment = (
                        f"Spec diff (v{diff.previous_version} -> v{page.version}):\n\n{diff.diff_text[:8000]}"
                    )
                    await deps.jira.add_comment(jira_issue.key, diff_comment)
                except Exception as exc:
                    logger.warning("Failed to comment the spec diff on Jira story %s: %s", jira_issue.key, exc)
            else:
                try:
                    jira_issue = await deps.jira.create_issue(
                        project_key=settings.jira_project_key,
                        issue_type=settings.jira_issue_type,
                        summary=story.summary,
                        description=story.description,
                        acceptance_criteria=story.acceptance_criteria,
                    )
                    logger.info("Created Jira story %s for page %s", jira_issue.key, page_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to create a Jira story for page %s; continuing without one: %s", page_id, exc
                    )
                    jira_issue = None

                if jira_issue is not None:
                    # The raw spec diff as a follow-up comment, not part of
                    # the description -- the description is meant to be
                    # readable at a glance (prose, from an LLM when one's
                    # configured, or a short plain-English note when it
                    # isn't); a unified diff dumped into that same field
                    # defeats that regardless of which one produced it. This
                    # comment is where the exact technical detail lives.
                    try:
                        if diff.is_first_seen:
                            diff_comment = f"Full current spec, as of v{page.version}:\n\n{diff.diff_text[:8000]}"
                        else:
                            diff_comment = (
                                f"Spec diff (v{diff.previous_version} -> v{page.version}):\n\n{diff.diff_text[:8000]}"
                            )
                        await deps.jira.add_comment(jira_issue.key, diff_comment)
                    except Exception as exc:
                        logger.warning(
                            "Failed to comment the spec diff on new Jira story %s: %s", jira_issue.key, exc
                        )
                    if settings.jira_suggest_story_points and story.complexity:
                        try:
                            await deps.jira.add_comment(
                                jira_issue.key,
                                f"AI-suggested complexity: {story.complexity} -- {story.complexity_reason}",
                            )
                        except Exception as exc:
                            logger.warning(
                                "Failed to comment AI-suggested complexity on Jira story %s: %s",
                                jira_issue.key, exc,
                            )

            if jira_issue is not None:
                # Durable even if everything below this point fails (change
                # engine crash, failing tests, ...) -- see
                # PageStore.remember_jira_issue's docstring for why this is a
                # merge, not the full put() that happens on a complete
                # success further down.
                deps.store.remember_jira_issue(page_id, jira_issue.key)

        return (page, diff, previous_page, jira_issue, jira_reused, already_approved)

    try:
        already_approved = False
        if resume is None:
            prologue = await _fetch_diff_and_story()
            if isinstance(prologue, PipelineResult):
                return await finish(prologue)
            page, diff, previous_page, jira_issue, jira_reused, already_approved = prologue
        else:
            # Resuming after JIRA_APPROVAL_REQUIRED approval -- reconstruct
            # exactly what was reviewed, from PendingApprovalStore, instead
            # of re-fetching/re-diffing the live page (see this function's
            # docstring for why that binding matters). previous_page still
            # comes from a fresh PageStore read: it's independent of
            # approval state and needed for PR-branch-reuse info below.
            page = PageSnapshot(
                page_id=resume["page_id"],
                title=resume["page_title"],
                version=resume["page_version"],
                body_html=resume["page_body_html"],
                url=resume["page_url"],
                labels=resume["page_labels"],
            )
            progress["page"] = page
            diff = PageDiff(
                page=page,
                previous_version=resume["previous_version"],
                diff_text=resume["diff_text"],
                is_first_seen=resume["is_first_seen"],
                body_checksum=resume["body_checksum"],
            )
            previous_page = deps.store.get(page_id)
            jira_issue = JiraIssueResult(key=resume["jira_issue_key"], url=resume["jira_issue_url"])
            jira_reused = True

        if settings.jira_approval_required and resume is None and jira_issue is not None and not already_approved:
            # Story now exists -- stop here instead of continuing into
            # cloning/implementation. pipeline/approval_poller.py is what
            # resumes this (see this function's `resume` param docstring).
            # Skipped entirely when already_approved: a reused story that's
            # already at settings.jira_approved_status_name (e.g. approved
            # via "Plan a Sprint" before this page's regular pipeline ever
            # ran) has nothing left to wait on -- falls straight through to
            # implementation below instead of parking a redundant
            # PendingApproval nothing would ever resume.
            deps.pending_approvals.put(
                {
                    "page_id": page.page_id,
                    "jira_issue_key": jira_issue.key,
                    "jira_issue_url": jira_issue.url,
                    "previous_version": diff.previous_version,
                    "diff_text": diff.diff_text,
                    "is_first_seen": diff.is_first_seen,
                    "body_checksum": diff.body_checksum,
                    "page_version": page.version,
                    "page_body_checksum": diff.body_checksum,
                    "page_title": page.title,
                    "page_url": page.url,
                    "page_body_html": page.body_html,
                    "page_labels": page.labels,
                    "run_id": run_id,
                    "created_at": started_at.isoformat(),
                }
            )
            logger.info(
                "Page %s: JIRA_APPROVAL_REQUIRED is on -- waiting for %s to reach status %r before implementing.",
                page_id, jira_issue.key, settings.jira_approved_status_name,
            )
            return await finish(
                PipelineResult(
                    status="awaiting_approval", page=page, diff=diff, jira_issue=jira_issue, jira_reused=jira_reused,
                )
            )

        # Which repo(s) is this change actually for -- see config.py's
        # resolved_repo_targets docstring for the label-matching semantics
        # (empty label = matches every page, same as CONFLUENCE_ALLOWED_
        # LABELS). Always at least one entry for every existing user (the
        # single-repo fallback), so "no repo matched" only happens for a
        # real multi-repo config where this page's labels don't select any
        # of them.
        page_labels = {label.lower() for label in page.labels}
        repo_targets = settings.resolved_repo_targets
        in_scope = [rt for rt in repo_targets if not rt.label or rt.label.lower() in page_labels]
        if not in_scope:
            logger.info(
                "Page %s labels %s don't match any configured repo's routing label; nothing to implement.",
                page_id, sorted(page_labels),
            )
            return await finish(
                PipelineResult(
                    status="no_repo_matched", page=page, diff=diff, jira_issue=jira_issue, jira_reused=jira_reused,
                )
            )

        # Nested per-repo subdirectories only for a genuinely multi-repo
        # config -- keeps the workspace layout (and thus repo_dir itself)
        # byte-identical to before this feature existed for every existing
        # single-repo user, rather than varying page-to-page depending on
        # how many configured repos happen to match a given page's labels.
        is_multi_repo = bool(settings.target_repos_json.strip())
        workspace = settings.workdirs_path / f"page-{page_id}-v{page.version}"
        repo_dirs: dict[str, Path] = {
            rt.target_repo: (workspace / _repo_slug(rt.target_repo)) if is_multi_repo else workspace
            for rt in in_scope
        }

        # Clone + branch setup per repo -- each repo's own PR-reuse check is
        # fully independent (see _setup_repo_branch); this must all happen
        # before the shared change-engine call below, since that call edits
        # whatever's already on disk.
        mark_stage("clone_repo")
        branch_info: dict[str, tuple[str, int | None, bool]] = {}
        for rt in in_scope:
            branch_info[rt.target_repo] = await _setup_repo_branch(
                deps, rt, repo_dirs[rt.target_repo], page_id, page, previous_page
            )

        if is_multi_repo:
            in_scope_names = {rt.target_repo for rt in in_scope}
            out_of_scope = [rt for rt in repo_targets if rt.target_repo not in in_scope_names]
            diff.repo_context = build_repo_context(
                in_scope, repo_dirs, workspace, out_of_scope, global_coding_standards=settings.coding_standards
            )
        elif len(in_scope) == 1:
            # Single-repo run -- no "which repos are checked out" framing
            # needed (there's only ever one), but tech_stack/coding_standards
            # on that one RepoTarget still deserve a prompt line if set. See
            # agent/prompts.py::build_single_repo_context's docstring for why
            # this stays None (today's behavior) when neither is configured.
            diff.repo_context = build_single_repo_context(in_scope[0], settings.coding_standards)

        # Self-correction loop: give the change engine up to N attempts,
        # feeding back the previous attempt's test failures (across every
        # touched repo) each retry, before giving up. Attempt 1 is today's
        # original behavior; CHANGE_AGENT_MAX_ATTEMPTS=1 disables retrying
        # entirely. One shared attempt budget across the whole (possibly
        # multi-repo) change, not per repo -- retrying just one repo in
        # isolation could reintroduce the cross-repo inconsistency this
        # design exists to prevent, and no PR opens for ANY repo unless the
        # whole joint edit's tests pass together (see the tests_failed
        # return below -- a partial success isn't a real success here).
        max_attempts = max(1, settings.change_agent_max_attempts)
        change: ChangeAgentResult | None = None
        repo_tests: dict[str, RepoTestResult] = {}
        attempt = 1
        retry_context: str | None = None

        for attempt in range(1, max_attempts + 1):
            mark_stage("ai_agent")
            change = await deps.change_engine.implement_change(
                workspace, diff, settings.change_agent_max_turns, retry_context=retry_context
            )

            if not change.success:
                logger.error(
                    "Change engine did not complete successfully for page %s (attempt %s/%s)",
                    page_id, attempt, max_attempts,
                )
                return await finish(
                    PipelineResult(
                        status="error", page=page, diff=diff, change=change, error="change agent failed",
                        attempts=attempt, jira_issue=jira_issue, jira_reused=jira_reused,
                    )
                )

            touched = {
                rt.target_repo: rt for rt in in_scope if await deps.git.has_changes(repo_dirs[rt.target_repo])
            }
            if not touched:
                logger.warning(
                    "Change agent made no file changes in any in-scope repo for page %s (attempt %s/%s)",
                    page_id, attempt, max_attempts,
                )
                return await finish(
                    PipelineResult(
                        status="error", page=page, diff=diff, change=change, error="agent made no file changes",
                        attempts=attempt, jira_issue=jira_issue, jira_reused=jira_reused,
                    )
                )

            mark_stage("run_tests")
            repo_tests = {}
            all_changed_files: list[str] = []
            for target_repo, rt in touched.items():
                repo_dir = repo_dirs[target_repo]
                files = await deps.git.changed_files(repo_dir)
                all_changed_files.extend(
                    f"{_repo_slug(target_repo)}/{f}" if is_multi_repo else f for f in files
                )
                repo_tests[target_repo] = await run_tests(repo_dir, rt.test_command)
                # Lint gate: only run once tests already passed (no point
                # linting code that's about to get rewritten by a retry
                # anyway), same pass/fail semantics and the exact same
                # retry loop below -- a lint failure feeds back into
                # retry_context precisely like a test failure would.
                if repo_tests[target_repo].passed and rt.lint_command:
                    lint_result = await run_tests(repo_dir, rt.lint_command)
                    if lint_result.crashed:
                        # Fails OPEN, unlike a real lint failure below --
                        # lint_command is optional, and a malformed/typo'd
                        # command is a config problem, not evidence the
                        # code is wrong. Blocking an otherwise-correct,
                        # tested PR over that would be worse than just
                        # skipping the gate and logging it.
                        logger.warning(
                            "Lint command for %s could not be run (%s); skipping the lint gate for this run: %s",
                            target_repo, rt.lint_command, lint_result.output,
                        )
                    elif not lint_result.passed:
                        repo_tests[target_repo] = RepoTestResult(
                            passed=False,
                            output=repo_tests[target_repo].output + "\n\n$ (lint)\n" + lint_result.output,
                            command=f"{rt.test_command} && {rt.lint_command}",
                        )
            change.files_changed = all_changed_files

            if all(t.passed for t in repo_tests.values()):
                break

            if attempt == max_attempts:
                logger.error(
                    "Tests still failing for page %s after %s attempt(s); not opening any PR.", page_id, attempt
                )
                failing = [target_repo for target_repo, t in repo_tests.items() if not t.passed]
                return await finish(
                    PipelineResult(
                        status="tests_failed", page=page, diff=diff, change=change,
                        tests=repo_tests[failing[0]], attempts=attempt,
                        jira_issue=jira_issue, jira_reused=jira_reused,
                        repo_results=[
                            RepoChangeResult(
                                target_repo=target_repo, status="tests_failed" if not t.passed else "no_changes",
                                tests=t,
                            )
                            for target_repo, t in repo_tests.items()
                        ],
                    )
                )

            logger.warning(
                "Tests failed for page %s on attempt %s/%s; retrying with the failures fed back to the agent.",
                page_id, attempt, max_attempts,
            )
            retry_context = "\n\n".join(
                f"[{target_repo}]\n{t.output[-4000:]}" for target_repo, t in repo_tests.items() if not t.passed
            )

        # Per-repo finalize: commit/push/open-or-update PR/judge. Each
        # repo's own failure (PR API error, judge call) only affects its own
        # RepoChangeResult -- see _finalize_repo and _aggregate_status.
        mark_stage("open_pr")
        repo_results: list[RepoChangeResult] = []
        for rt in in_scope:
            if rt.target_repo not in repo_tests:
                # In scope for this page, but the agent decided nothing
                # needed to change there this run.
                repo_results.append(RepoChangeResult(target_repo=rt.target_repo, status="no_changes"))
                continue
            branch_name, reuse_pr_number, reused = branch_info[rt.target_repo]
            repo_results.append(
                await _finalize_repo(
                    deps, rt, repo_dirs[rt.target_repo], branch_name, reuse_pr_number, reused,
                    page, diff, change, settings.judge_enabled,
                )
            )

        # A durable artifact (a PR) now exists for whichever repos
        # succeeded, so the store advances for those regardless of any
        # other repo's verdict/failure -- a retry from the same diff would
        # otherwise re-attempt (and potentially duplicate) an
        # already-successful repo's PR too. Merges with whatever repo_prs a
        # previous run left for repos NOT touched this time (see
        # _repo_pr_info's docstring) so their reuse tracking isn't lost.
        opened_results = [r for r in repo_results if r.pull_request]
        if opened_results:
            merged_repo_prs: dict = dict((previous_page or {}).get("repo_prs") or {})
            for r in opened_results:
                merged_repo_prs[r.target_repo] = {
                    "open_pr_number": r.pull_request.number,
                    "open_pr_branch": r.pull_request.branch,
                }
            primary = opened_results[0].pull_request
            deps.store.put(
                StoredPage(
                    page_id=page.page_id,
                    title=page.title,
                    version=page.version,
                    body_html=page.body_html,
                    body_checksum=diff.body_checksum,
                    url=page.url,
                    open_pr_number=primary.number,
                    open_pr_branch=primary.branch,
                    repo_prs=merged_repo_prs,
                    jira_issue_key=jira_issue.key if jira_issue else None,
                )
            )

        overall_status = _aggregate_status(repo_results)

        # Best-effort and deliberately isolated, same reasoning as before:
        # PRs are already open (for whichever repos succeeded) at this
        # point, so a broken/unconfigured email step must not turn an
        # otherwise successful run into a reported failure. One email per
        # cleanly-opened/updated PR -- simpler and just as actionable as a
        # single combined email, without needing a new multi-PR email
        # template. Deliberately excludes judge_rejected repos, same as the
        # single-repo behavior this replaces -- the judge's Jira comment
        # already covers "needs review", a separate "PR opened" email would
        # read as a false all-clear.
        mark_stage("send_email")
        email_sent = False
        email_error: str | None = None
        email_targets = [r for r in repo_results if r.status == "opened_pr"]
        if email_targets:
            email_key = (
                settings.postmark_api_key
                if settings.email_provider.strip().lower() == "postmark"
                else settings.sendgrid_api_key
            )
            if not email_key:
                email_error = f"No API key configured for email provider '{settings.email_provider}'; skipped"
                logger.info("Skipping summary email for page %s: %s", page_id, email_error)
            else:
                any_sent = False
                last_error: str | None = None
                for r in email_targets:
                    try:
                        subject, text_body, html_body = build_summary_email(diff, change, r.pull_request)
                        await deps.email_client.send_email(
                            from_address=settings.email_from_address,
                            to_addresses=settings.email_to_list,
                            subject=subject,
                            text_body=text_body,
                            html_body=html_body,
                        )
                        any_sent = True
                    except Exception as exc:
                        last_error = str(exc)
                        logger.warning(
                            "PR #%s (%s) opened but the summary email failed to send: %s",
                            r.pull_request.number, r.target_repo, exc,
                        )
                email_sent = any_sent
                email_error = last_error

        for r in repo_results:
            if r.pull_request:
                logger.info(
                    "%s PR #%s for %s on page %s: %s",
                    "Updated" if r.reused_pr else "Opened", r.pull_request.number, r.target_repo, page_id,
                    r.pull_request.url,
                )
            elif r.status == "error":
                logger.error("Failed to finalize %s for page %s: %s", r.target_repo, page_id, r.error)

        return await finish(
            PipelineResult(
                status=overall_status,
                page=page,
                diff=diff,
                change=change,
                judge=next((r.judge for r in repo_results if r.judge), None),
                pull_request=opened_results[0].pull_request if opened_results else None,
                reused_pr=opened_results[0].reused_pr if opened_results else False,
                # A backward-compat single summary string for the existing
                # single-repo-shaped UI (run_detail.html's "Error" row) --
                # the full per-repo detail is always in repo_results.
                error=(
                    "; ".join(f"{r.target_repo}: {r.error}" for r in repo_results if r.error) or None
                ),
                email_sent=email_sent,
                email_error=email_error,
                attempts=attempt,
                jira_issue=jira_issue,
                jira_reused=jira_reused,
                repo_results=repo_results,
            )
        )

    except Exception as exc:
        logger.exception("Pipeline failed for page %s", page_id)
        return await finish(PipelineResult(status="error", error=str(exc), jira_issue=jira_issue, jira_reused=jira_reused))
    finally:
        if owns_deps:
            await deps.confluence.aclose()
            await deps.github.aclose()
            await deps.email_client.aclose()
