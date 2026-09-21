"""Jira polling, LLM triage, and explicit code-fix orchestration."""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from confluence_pr_agent.agent.factory import build_change_engine
from confluence_pr_agent.config import Settings
from confluence_pr_agent.jira.client import JiraClient
from confluence_pr_agent.models import ChangeAgentResult, PageDiff, PageSnapshot, RepoTestResult, RunRecord
from confluence_pr_agent.repo.git_client import GitClient
from confluence_pr_agent.repo.github_client import GitHubClient
from confluence_pr_agent.storage.run_store import RunStore
from confluence_pr_agent.testing.test_runner import run_tests


async def _implement_with_configured_engine(
    settings: Settings, repo_dir: Path, diff: PageDiff
) -> ChangeAgentResult:
    """Run the engine selected by CHANGE_AGENT_ENGINE, without provider fallback."""
    engine = build_change_engine(settings)
    return await engine.implement_change(repo_dir, diff, settings.change_agent_max_turns)


def _store_path(settings: Settings) -> Path:
    return settings.data_dir_path / "jira_bug_scans.json"


def load_scan(settings: Settings) -> dict:
    path = _store_path(settings)
    if not path.exists():
        return {"scanned_at": None, "issues": []}
    return json.loads(path.read_text())


def _save_scan(settings: Settings, issues: list[dict]) -> dict:
    result = {"scanned_at": datetime.now(timezone.utc).isoformat(), "issues": issues}
    settings.data_dir_path.mkdir(parents=True, exist_ok=True)
    _store_path(settings).write_text(json.dumps(result, indent=2))
    return result


def clear_analysis_display(settings: Settings, issue_key: str | None = None) -> dict:
    """Remove previously generated analysis text while preserving workflow state."""
    scan = load_scan(settings)
    for issue in scan.get("issues", []):
        if issue_key and issue.get("key") != issue_key:
            continue
        for field in ("analysis", "analysis_repos", "analysis_summary", "analysis_plan"):
            issue.pop(field, None)
    return _save_scan(settings, scan.get("issues", []))


def _find_issue(settings: Settings, issue_key: str) -> tuple[dict, dict]:
    scan = load_scan(settings)
    issue = next((item for item in scan["issues"] if item["key"] == issue_key), None)
    if not issue:
        raise ValueError("Issue is not in the latest Jira scan")
    return scan, issue


async def analyze_issue(settings: Settings, issue_key: str) -> dict:
    """Create a reviewable implementation plan without changing any repo."""
    scan, issue = _find_issue(settings, issue_key)
    labels = {label.strip().lower() for label in issue.get("labels", [])}
    targets = settings.resolved_repo_targets
    matched = [target for target in targets if not target.label or target.label.lower() in labels]
    if not matched:
        matched = targets
    repo_names = [target.target_repo for target in matched]
    repo_label = "repository" if len(repo_names) == 1 else "repositories"
    issue["triage"] = "analyzed"
    issue["analysis_repos"] = repo_names
    issue["analysis_summary"] = (
        f"The bug will be investigated in {len(repo_names)} configured {repo_label}: "
        f"{', '.join(repo_names)}."
    )
    issue["analysis_plan"] = [
        f"Inspect the relevant code and tests in {target.target_repo}." for target in matched
    ] + [
        "Reproduce the reported behavior from the Jira description.",
        "Implement the smallest fix and add or update regression tests.",
        "Run the configured test command, then open a pull request for review.",
    ]
    issue["analysis"] = issue["analysis_summary"] + "\n\n" + "\n".join(
        f"{index}. {step}" for index, step in enumerate(issue["analysis_plan"], 1)
    )
    github = GitHubClient(settings.github_token)
    files_by_repo: dict[str, list[str]] = {}
    try:
        description_text = _description_text(issue.get("description"))
        keywords = {
            word.lower() for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_/-]{2,}", issue["summary"] + " " + description_text)
        }
        for target in matched:
            try:
                tree = await github.get_repo_file_tree(target.target_repo, target.base_branch)
            except Exception:
                tree = []
            matching_paths = [path for path in tree if any(keyword in path.lower() for keyword in keywords)]
            test_paths = [
                path for path in tree
                if "/test" in path.lower() or path.lower().startswith("test")
            ]
            implementation_paths = [path for path in matching_paths if path not in test_paths]
            candidates = (implementation_paths + test_paths)[:12]
            files_by_repo[target.target_repo] = candidates or tree[:12]
    finally:
        await github.aclose()
    issue["analysis_files_by_repo"] = files_by_repo
    file_lines = []
    for repo, files in files_by_repo.items():
        file_lines.append(f"#### {repo}")
        file_lines.extend(f"- {path}" for path in files)
    issue["analysis"] += "\n\n### Candidate files to inspect/change\n" + "\n".join(file_lines)
    client = JiraClient(settings.jira_base_url, settings.jira_user_email, settings.jira_api_token)
    try:
        await client.add_comment(
            issue_key,
            "Implementation plan from Driftbridge:\n\n"
            + issue["analysis"]
            + "\n\nA developer can approve this bug by moving it to the configured approved status."
        )
    finally:
        await client.aclose()
    _save_scan(settings, scan["issues"])
    return issue


def _description_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_description_text(item) for item in value.get("content", []))
    if isinstance(value, list):
        return " ".join(_description_text(item) for item in value)
    return str(value or "")


async def poll_jira(settings: Settings) -> dict:
    client = JiraClient(settings.jira_base_url, settings.jira_user_email, settings.jira_api_token)
    try:
        await client.test_connection()
        jql = settings.jira_bug_jql or (
            f'project = "{settings.jira_project_key}" AND issuetype = Bug '
            "ORDER BY updated DESC"
        )
        previous = {issue.get("key"): issue for issue in load_scan(settings).get("issues", [])}
        issues = await client.search_issues(jql, settings.jira_poll_limit)
        returned_keys = {issue.get("key") for issue in issues}
        # A workflow transition can make an analyzed bug fall outside the
        # configured search JQL. Re-fetch analyzed tickets by key so approval
        # polling never loses the plan or the ticket from the local dashboard.
        for key, old_issue in previous.items():
            if key and key not in returned_keys and old_issue.get("triage") == "analyzed":
                try:
                    status = await client.get_issue_status(key)
                except Exception:
                    continue
                retained = dict(old_issue)
                retained["status"] = status.status_name
                issues.append(retained)
        for issue in issues:
            old_issue = previous.get(issue.get("key"), {})
            issue["triage"] = issue.get("triage") or old_issue.get("triage") or "pending"
            issue["analysis"] = issue.get("analysis") or old_issue.get("analysis") or ""
            for field in ("triage", "analysis", "pr_url", "analysis_repos", "analysis_summary", "analysis_plan", "analysis_files_by_repo"):
                if not issue.get(field) and old_issue.get(field):
                    issue[field] = old_issue[field]
        return _save_scan(settings, issues)
    finally:
        await client.aclose()


def create_fix_run(settings: Settings, issue: dict) -> str:
    """Create the visible Pipeline Runs placeholder before an agent starts."""
    run_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(timezone.utc).isoformat()
    target_repo = ", ".join(target.target_repo for target in settings.resolved_repo_targets)
    RunStore(settings.runs_store_path).upsert_run(RunRecord(
        run_id=run_id,
        started_at=started_at,
        finished_at=started_at,
        duration_seconds=0,
        page_id=issue["key"],
        page_title=issue["summary"],
        confluence_url=issue.get("url", ""),
        engine=settings.change_agent_engine,
        target_repo=target_repo,
        status="running",
        current_stage="fetch_page",
        jira_issue_key=issue["key"],
        jira_issue_url=issue.get("url"),
        max_attempts=settings.change_agent_max_attempts,
    ))
    issue["triage"] = "running"
    return run_id


async def fix_issue(settings: Settings, issue_key: str, run_id: str | None = None) -> dict:
    scan, issue = _find_issue(settings, issue_key)
    issue["triage"] = "running"
    _save_scan(settings, scan["issues"])
    target = settings.resolved_repo_targets[0]
    started_at = datetime.now(timezone.utc)
    start_clock = time.monotonic()
    store = RunStore(settings.runs_store_path) if run_id else None

    def record(stage: str, status: str = "running", **values) -> None:
        if not store or not run_id:
            return
        store.upsert_run(RunRecord(
            run_id=run_id,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.monotonic() - start_clock,
            page_id=issue_key,
            page_title=issue["summary"],
            confluence_url=issue.get("url", ""),
            engine=settings.change_agent_engine,
            target_repo=target.target_repo,
            status=status,
            current_stage=stage,
            jira_issue_key=issue_key,
            jira_issue_url=issue.get("url"),
            max_attempts=settings.change_agent_max_attempts,
            **values,
        ))

    try:
        record("clone_repo")
        repo_dir = settings.workdirs_path / f"jira-{issue_key.lower()}"
        git = GitClient(settings.github_token)
        await git.clone(target.target_repo, repo_dir, target.base_branch)
        # A previous attempt may already have pushed the predictable Jira
        # branch. Use a unique branch so retries never overwrite or diverge
        # from work that is already on the remote.
        branch = f"jira-bug/{issue_key.lower()}-{run_id or uuid.uuid4().hex[:8]}"
        await git.create_branch(repo_dir, branch)
        description = issue.get("description") or "No Jira description provided."
        diff = PageDiff(
            page=PageSnapshot(issue_key, issue["summary"], 1, description, issue["url"], issue.get("labels", [])),
            previous_version=None,
            diff_text=f"Jira bug ticket {issue_key}: {issue['summary']}\n\n{description}\n\nImplement the bug fix and add or update tests.",
            is_first_seen=True,
            body_checksum=issue_key,
        )
        record("ai_agent")
        result = await _implement_with_configured_engine(settings, repo_dir, diff)
        if not result.success or not await git.has_changes(repo_dir):
            issue["triage"] = "fix_failed"
            details = (result.summary or "The selected change engine did not produce a usable fix.").strip()
            if result.raw_log:
                details += "\n\nEngine log:\n" + result.raw_log[-3000:]
            issue["analysis"] = details[:4000]
            _save_scan(settings, scan["issues"])
            record("ai_agent", "error", summary=issue["analysis"], raw_log=result.raw_log[-4000:])
            return issue
        record("run_tests")
        tests: RepoTestResult = await run_tests(repo_dir, target.test_command)
        if not tests.passed:
            issue["triage"] = "tests_failed"
            issue["analysis"] = tests.output[-2000:]
            _save_scan(settings, scan["issues"])
            record("run_tests", "tests_failed", summary=issue["analysis"], test_output=tests.output[-4000:])
            return issue
        record("open_pr")
        await git.commit_all(repo_dir, f"fix: resolve Jira {issue_key}")
        await git.push(repo_dir, branch)
        github = GitHubClient(settings.github_token)
        try:
            pr = await github.open_pull_request(
                target.target_repo, branch, target.base_branch,
                f"fix: resolve {issue_key} - {issue['summary']}",
                f"Closes [{issue_key}]({issue['url']}).\n\nImplemented and tested by the Jira bug fixer.",
            )
        finally:
            await github.aclose()
        issue["triage"] = "pr_opened"
        issue["pr_url"] = pr.url
        issue["analysis"] = result.summary
        _save_scan(settings, scan["issues"])
        record("open_pr", "opened_pr", summary=result.summary, pr_number=pr.number, pr_url=pr.url,
               files_changed=result.files_changed, raw_log=result.raw_log[-4000:])
        return issue
    except Exception as exc:
        issue["triage"] = "fix_failed"
        issue["analysis"] = str(exc)[:4000]
        _save_scan(settings, scan["issues"])
        record("ai_agent", "error", summary=issue["analysis"], error=str(exc)[:4000])
        raise