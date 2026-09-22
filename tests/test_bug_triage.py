from __future__ import annotations

import json

from confluence_pr_agent.config import Settings
from confluence_pr_agent.jira import bug_triage


async def test_poll_jira_saves_open_bugs_and_preserves_workflow_fields(tmp_path, monkeypatch):
    class FakeJiraClient:
        def __init__(self, *args):
            self.jql = None

        async def test_connection(self):
            return "Demo User"

        async def search_issues(self, jql, limit):
            self.jql = jql
            return [{"key": "KAN-2", "summary": "Broken checkout"}]

        async def aclose(self):
            pass

    settings = Settings(
        data_dir=str(tmp_path),
        jira_project_key="KAN",
        jira_base_url="https://jira.example.com",
    )
    settings.data_dir_path.mkdir(parents=True, exist_ok=True)
    (settings.data_dir_path / "jira_bug_scans.json").write_text(json.dumps({
        "scanned_at": "yesterday",
        "issues": [{"key": "KAN-2", "triage": "fix_failed", "analysis": "needs retry", "pr_url": ""}],
    }))
    clients = []

    def make_client(*args):
        client = FakeJiraClient(*args)
        clients.append(client)
        return client

    monkeypatch.setattr(bug_triage, "JiraClient", make_client)

    scan = await bug_triage.poll_jira(settings)

    assert "issuetype = Bug" in clients[0].jql
    assert scan["issues"] == [{
        "key": "KAN-2",
        "summary": "Broken checkout",
        "triage": "fix_failed",
        "analysis": "needs retry",
    }]
    assert json.loads((tmp_path / "jira_bug_scans.json").read_text()) == scan


async def test_analyze_issue_records_repositories_and_action_plan(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=str(tmp_path),
        target_repo="acme/widgets",
        target_repo_base_branch="main",
        target_repo_test_command="pytest",
    )
    (tmp_path / "jira_bug_scans.json").write_text(json.dumps({
        "scanned_at": "now",
        "issues": [{
            "key": "KAN-2",
            "summary": "Broken checkout",
            "description": "A checkout request returns 500.",
            "url": "https://jira.example/KAN-2",
            "labels": [],
            "triage": "pending",
        }],
    }))
    comments = []

    class FakeJiraClient:
        def __init__(self, *args):
            pass

        async def add_comment(self, issue_key, comment):
            comments.append((issue_key, comment))

        async def aclose(self):
            pass

    class FakeGitHubClient:
        def __init__(self, *args):
            pass

        async def get_repo_file_tree(self, owner_repo, branch):
            return ["src/booking_service.py", "tests/test_booking_service.py", "README.md"]

        async def aclose(self):
            pass

    monkeypatch.setattr(bug_triage, "JiraClient", FakeJiraClient)
    monkeypatch.setattr(bug_triage, "GitHubClient", FakeGitHubClient)

    issue = await bug_triage.analyze_issue(settings, "KAN-2")

    assert issue["triage"] == "analyzed"
    assert issue["analysis_repos"] == ["acme/widgets"]
    assert any("regression tests" in step for step in issue["analysis_plan"])
    assert issue["analysis_files_by_repo"]["acme/widgets"] == ["tests/test_booking_service.py"]
    assert comments and comments[0][0] == "KAN-2"
    assert "Implementation plan from Driftbridge" in comments[0][1]