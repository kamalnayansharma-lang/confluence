from __future__ import annotations

import hashlib
import hmac
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from confluence_pr_agent.config import get_process_config, get_settings
from confluence_pr_agent.storage.run_store import RunStore
from confluence_pr_agent.ui import routes as ui_routes
from confluence_pr_agent.webhook.app import app


@pytest.fixture(autouse=True)
def _isolated_env_file(tmp_path, monkeypatch):
    """Points DATA_DIR at a throwaway directory and gives every test a
    fixed DEFAULT_USER identity ("testuser") -- otherwise every test in
    this module would read/write the real project's live data/users/*/
    state. INTERNAL_SHARED_SECRET is explicitly blanked (not just left
    unset) so current_username's secret check is skipped, same as real
    local dev without Caddy in front -- explicit matters here because the
    real top-level .env has a real secret in it, and monkeypatch.setenv's
    os.environ value only wins over that file's value if it's actually
    set to something (pydantic-settings' env source outranks its
    dotenv-file source, but only for keys os.environ actually has).
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEFAULT_USER", "testuser")
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", "")

    get_process_config.cache_clear()
    get_settings.cache_clear()
    yield get_process_config().user_env_path("testuser")
    get_process_config.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_config_form_renders(client):
    resp = client.get("/ui/config")
    assert resp.status_code == 200
    assert "Configuration" in resp.text
    assert "CHANGE_AGENT_ENGINE" in resp.text


def test_config_form_marks_github_token_as_repo_specific_for_client_side_toggling(client):
    """GITHUB_TOKEN must carry data-repo-key so config.html's syncRepoFields()
    can hide it when REPO_PROVIDER isn't "github" -- same pattern already
    used for engine credentials (ANTHROPIC_API_KEY etc. hidden unless their
    engine is selected). Only the data attribute/markup is testable here;
    the actual show/hide is client-side JS, exercised visually, not by this
    server-rendered-HTML test.
    """
    resp = client.get("/ui/config")
    assert 'data-repo-key="GITHUB_TOKEN"' in resp.text
    assert "REPO_CREDENTIAL_BY_PROVIDER" in resp.text
    assert '"github": "GITHUB_TOKEN"' in resp.text


def test_config_form_renders_taglist_editor_for_allowed_labels(client, _isolated_env_file):
    client.post("/ui/config", data={"CONFLUENCE_ALLOWED_LABELS": "brd,spec-for-agent"})

    resp = client.get("/ui/config")

    assert 'id="CONFLUENCE_ALLOWED_LABELS"' in resp.text
    assert 'value="brd,spec-for-agent"' in resp.text  # non-secret, so redisplayed via the hidden input
    assert "taglist-editor" in resp.text


def test_saving_config_writes_env_file(client, _isolated_env_file):
    resp = client.post(
        "/ui/config",
        data={"TARGET_REPOS_JSON": '[{"target_repo": "acme/widgets"}]', "CHANGE_AGENT_ENGINE": "cursor"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/config?saved=1"

    content = _isolated_env_file.read_text()
    assert 'TARGET_REPOS_JSON=[{"target_repo": "acme/widgets"}]' in content
    assert "CHANGE_AGENT_ENGINE=cursor" in content


def test_saving_config_survives_env_file_being_unrenamable(client, _isolated_env_file, monkeypatch):
    # Regression test: .env is bind-mounted as a single file into the
    # container, so any write path that renames a temp file onto it (like
    # python-dotenv's set_key) raises "Device or resource busy" -- os.replace
    # is disallowed onto a bind-mount target. Saving must write the file's
    # contents in place instead of ever calling replace/rename.
    def _boom(*args, **kwargs):
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(os, "replace", _boom)
    monkeypatch.setattr(os, "rename", _boom)

    resp = client.post(
        "/ui/config",
        data={"TARGET_REPOS_JSON": '[{"target_repo": "acme/widgets"}]'},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    content = _isolated_env_file.read_text()
    assert 'TARGET_REPOS_JSON=[{"target_repo": "acme/widgets"}]' in content


def test_blank_secret_field_does_not_clear_existing_value(client, _isolated_env_file):
    client.post("/ui/config", data={"GITHUB_TOKEN": "ghp_realvalue"})
    client.post("/ui/config", data={"GITHUB_TOKEN": ""})

    content = _isolated_env_file.read_text()
    assert "GITHUB_TOKEN=ghp_realvalue" in content


def test_secret_value_is_never_redisplayed_in_form(client, _isolated_env_file):
    client.post("/ui/config", data={"GITHUB_TOKEN": "ghp_supersecret"})

    resp = client.get("/ui/config")
    assert "ghp_supersecret" not in resp.text


def test_blank_number_field_does_not_corrupt_env_file(client, _isolated_env_file):
    """Regression test: a blank CHANGE_AGENT_MAX_ATTEMPTS/CONFLUENCE_POLL_
    INTERVAL_SECONDS previously got written straight to .env as
    KEY= -- which Settings() can't parse as an int, breaking get_settings()
    (and therefore nearly every route) until someone edits the file by hand.
    A blank number field must behave like a blank secret field: leave the
    existing value alone.
    """
    client.post("/ui/config", data={"CHANGE_AGENT_MAX_ATTEMPTS": "5"})
    client.post("/ui/config", data={"CHANGE_AGENT_MAX_ATTEMPTS": ""})

    content = _isolated_env_file.read_text()
    assert "CHANGE_AGENT_MAX_ATTEMPTS=5" in content
    assert "CHANGE_AGENT_MAX_ATTEMPTS=\n" not in content

    # Settings() must still parse cleanly after the save -- the actual
    # symptom of the original bug.
    get_settings.cache_clear()
    get_settings("testuser")


def test_non_numeric_value_for_a_number_field_is_rejected(client, _isolated_env_file):
    client.post("/ui/config", data={"CHANGE_AGENT_MAX_ATTEMPTS": "3"})
    client.post("/ui/config", data={"CHANGE_AGENT_MAX_ATTEMPTS": "not-a-number"})

    content = _isolated_env_file.read_text()
    assert "CHANGE_AGENT_MAX_ATTEMPTS=3" in content


def test_jira_bug_fix_requires_analysis_and_starts_background_run(client, monkeypatch):
    settings = get_settings("testuser")
    settings.jira_approval_required = True
    settings.jira_approved_status_name = "Approved"
    settings.data_dir_path.mkdir(parents=True, exist_ok=True)
    (settings.data_dir_path / "jira_bug_scans.json").write_text(json.dumps({"scanned_at": "now", "issues": [{
        "key": "KAN-2", "summary": "Broken checkout", "url": "https://jira/KAN-2",
        "status": "Approved", "priority": "Medium", "triage": "analyzed",
    }]}))
    calls = []

    async def fake_fix_issue(settings, issue_key, run_id):
        calls.append((issue_key, run_id))

    monkeypatch.setattr(ui_routes, "fix_issue", fake_fix_issue)

    resp = client.post("/ui/jira-bugs/KAN-2/fix", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/jira-bugs?started=1&run_id=")
    assert calls[0][0] == "KAN-2"
    assert calls[0][1]


def test_jira_bug_analysis_remains_visible_after_fix_workflow_starts(client):
    settings = get_settings("testuser")
    settings.data_dir_path.mkdir(parents=True, exist_ok=True)
    (settings.data_dir_path / "jira_bug_scans.json").write_text(json.dumps({"scanned_at": "now", "issues": [{
        "key": "KAN-2",
        "summary": "Broken checkout",
        "status": "Approved",
        "priority": "Medium",
        "triage": "pr_opened",
        "analysis": "Fix the checkout bug by validating the request payload.",
        "analysis_repos": ["acme/widgets"],
        "analysis_plan": ["Inspect request validation", "Patch the handler"],
        "pr_url": "https://github.example/acme/widgets/pull/41",
    }]}))

    resp = client.get("/ui/jira-bugs")

    assert resp.status_code == 200
    assert "Fix the checkout bug by validating the request payload." in resp.text
    assert "acme/widgets" in resp.text
    assert "Open PR" in resp.text


def test_jira_bug_poll_turns_unauthorized_response_into_actionable_message(client, monkeypatch):
    request = httpx.Request("GET", "https://jira.example.com/rest/api/3/myself")
    response = httpx.Response(401, request=request)

    async def fail_poll(settings):
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr(ui_routes, "poll_jira", fail_poll)

    resp = client.post("/ui/jira-bugs/poll", follow_redirects=False)

    assert resp.status_code == 303
    assert "Authentication%20failed" in resp.headers["location"]


def test_runs_list_empty_state(client):
    resp = client.get("/ui/runs")
    assert resp.status_code == 200
    assert "No runs yet" in resp.text


def test_runs_list_filter_dropdowns_show_every_supported_engine_and_status_even_with_no_history(client):
    """The filter dropdowns must list everything the pipeline could ever
    produce, not just what happens to already be in run history -- otherwise
    they're empty right after a `Clear all runs`, which defeats the point.
    """
    resp = client.get("/ui/runs")

    for engine in ["claude_code", "cursor", "copilot", "codex", "gemini", "antigravity"]:
        assert f'value="{engine}"' in resp.text
    for status in ["running", "opened_pr", "tests_failed", "error", "no_change_detected"]:
        assert f'value="{status}"' in resp.text


def test_status_dropdown_uses_readable_labels_not_raw_values(client):
    resp = client.get("/ui/runs")

    assert '<option value="opened_pr"' in resp.text
    assert ">Open PR</option>" in resp.text
    assert ">Running</option>" in resp.text
    assert ">Tests Failed</option>" in resp.text
    assert ">No Change</option>" in resp.text
    assert ">Ignored (label)</option>" in resp.text


def test_ignored_run_renders_with_reason_not_as_a_red_error(client):
    from confluence_pr_agent.models import RunRecord

    store = RunStore(get_settings("testuser").runs_store_path)
    store.upsert_run(
        RunRecord(
            run_id="ignored-1",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:01+00:00",
            duration_seconds=0.4,
            page_id="77",
            page_title="Team Meeting Notes",
            confluence_url="https://example.com/77",
            engine="gemini",
            target_repo="acme/widgets",
            status="ignored",
            current_stage="fetch_page",
            error="Page has none of the required labels: brd, spec-for-agent",
        )
    )

    list_resp = client.get("/ui/runs")
    assert "Ignored (label)" in list_resp.text

    detail_resp = client.get("/ui/runs/ignored-1")
    assert "Reason" in detail_resp.text
    assert "none of the required labels" in detail_resp.text
    assert "<th>Error</th>" not in detail_resp.text


def test_runs_list_renders_a_recorded_run(client):
    settings = get_settings("testuser")
    store = RunStore(settings.runs_store_path)
    from confluence_pr_agent.models import RunRecord

    store.add_run(
        RunRecord(
            run_id="abc123",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00",
            duration_seconds=5.2,
            page_id="42",
            page_title="Some Spec",
            confluence_url="https://example.atlassian.net/wiki/pages/42",
            engine="claude_code",
            target_repo="acme/widgets",
            status="opened_pr",
            pr_number=9,
            pr_url="https://github.com/acme/widgets/pull/9",
        )
    )

    resp = client.get("/ui/runs")
    assert resp.status_code == 200
    assert "Some Spec" in resp.text
    assert "#9" in resp.text
    assert "https://github.com/acme/widgets/pull/9" in resp.text
    assert "claude_code" in resp.text
    assert 'href="/ui/runs/abc123"' in resp.text


def test_runs_list_shows_jira_story_and_flags_a_reused_one(client):
    settings = get_settings("testuser")
    store = RunStore(settings.runs_store_path)
    from confluence_pr_agent.models import RunRecord

    store.add_run(
        RunRecord(
            run_id="abc123",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00",
            duration_seconds=5.2,
            page_id="42",
            page_title="Some Spec",
            confluence_url="https://example.atlassian.net/wiki/pages/42",
            engine="claude_code",
            target_repo="acme/widgets",
            status="opened_pr",
            jira_issue_key="KAN-33",
            jira_issue_url="https://example.atlassian.net/browse/KAN-33",
            jira_reused=True,
        )
    )

    resp = client.get("/ui/runs")
    assert resp.status_code == 200
    assert "KAN-33" in resp.text
    assert "(reused)" in resp.text
    assert 'href="https://example.atlassian.net/browse/KAN-33"' in resp.text


def test_runs_list_hides_awaiting_approval_runs_when_approval_required(client):
    settings = get_settings("testuser")
    settings.jira_approval_required = True
    store = RunStore(settings.runs_store_path)
    from confluence_pr_agent.models import RunRecord

    store.add_run(
        RunRecord(
            run_id="run-awaiting", started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00", duration_seconds=5.0,
            page_id="1", page_title="Awaiting story", confluence_url="https://example.com/1",
            engine="claude_code", target_repo="acme/widgets", status="awaiting_approval",
        )
    )
    store.add_run(
        RunRecord(
            run_id="run-done", started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00", duration_seconds=5.0,
            page_id="2", page_title="Finished story", confluence_url="https://example.com/2",
            engine="claude_code", target_repo="acme/widgets", status="opened_pr",
        )
    )

    resp = client.get("/ui/runs")
    assert resp.status_code == 200
    assert "Finished story" in resp.text
    assert "Awaiting story" not in resp.text
    assert "1 run awaiting Jira approval is hidden" in resp.text
    assert 'href="/ui/runs?status=awaiting_approval"' in resp.text

    # Explicitly asking for that status still shows it.
    resp2 = client.get("/ui/runs?status=awaiting_approval")
    assert "Awaiting story" in resp2.text


def test_runs_list_shows_awaiting_approval_runs_when_approval_not_required(client):
    settings = get_settings("testuser")
    settings.jira_approval_required = False
    store = RunStore(settings.runs_store_path)
    from confluence_pr_agent.models import RunRecord

    store.add_run(
        RunRecord(
            run_id="run-awaiting", started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00", duration_seconds=5.0,
            page_id="1", page_title="Awaiting story", confluence_url="https://example.com/1",
            engine="claude_code", target_repo="acme/widgets", status="awaiting_approval",
        )
    )

    resp = client.get("/ui/runs")
    assert resp.status_code == 200
    assert "Awaiting story" in resp.text


def _seed_runs(store) -> None:
    from confluence_pr_agent.models import RunRecord

    store.add_run(
        RunRecord(
            run_id="run-claude-ok",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00",
            duration_seconds=5.0,
            page_id="1",
            page_title="Claude run",
            confluence_url="https://example.com/1",
            engine="claude_code",
            target_repo="acme/widgets",
            status="opened_pr",
        )
    )
    store.add_run(
        RunRecord(
            run_id="run-gemini-fail",
            started_at="2026-02-15T00:00:00+00:00",
            finished_at="2026-02-15T00:00:05+00:00",
            duration_seconds=5.0,
            page_id="2",
            page_title="Gemini run",
            confluence_url="https://example.com/2",
            engine="gemini",
            target_repo="acme/widgets",
            status="tests_failed",
        )
    )


def test_runs_list_filters_by_engine(client):
    _seed_runs(RunStore(get_settings("testuser").runs_store_path))

    resp = client.get("/ui/runs", params={"engine": "gemini"})

    assert "Gemini run" in resp.text
    assert "Claude run" not in resp.text


def test_runs_list_filters_by_status(client):
    _seed_runs(RunStore(get_settings("testuser").runs_store_path))

    resp = client.get("/ui/runs", params={"status": "opened_pr"})

    assert "Claude run" in resp.text
    assert "Gemini run" not in resp.text


def test_runs_list_filters_by_date_range(client):
    _seed_runs(RunStore(get_settings("testuser").runs_store_path))

    resp = client.get("/ui/runs", params={"date_from": "2026-02-01"})

    assert "Gemini run" in resp.text
    assert "Claude run" not in resp.text


def test_runs_list_shows_no_match_message_when_filters_exclude_everything(client):
    _seed_runs(RunStore(get_settings("testuser").runs_store_path))

    resp = client.get("/ui/runs", params={"engine": "codex"})

    assert "No runs match these filters" in resp.text


def test_runs_list_shows_a_running_run_without_a_duration(client):
    from confluence_pr_agent.models import RunRecord

    store = RunStore(get_settings("testuser").runs_store_path)
    store.upsert_run(
        RunRecord(
            run_id="run-in-progress",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:00+00:00",
            duration_seconds=0.0,
            page_id="99",
            page_title="",
            confluence_url="",
            engine="gemini",
            target_repo="acme/widgets",
            status="running",
        )
    )

    resp = client.get("/ui/runs")

    assert resp.status_code == 200
    assert "badge-running" in resp.text
    assert "0.0s" not in resp.text  # duration suppressed while running, not shown as a real 0.0s result


def test_run_detail_page_shows_in_progress_banner_for_a_running_run(client):
    from confluence_pr_agent.models import RunRecord

    store = RunStore(get_settings("testuser").runs_store_path)
    store.upsert_run(
        RunRecord(
            run_id="run-in-progress",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:00+00:00",
            duration_seconds=0.0,
            page_id="99",
            page_title="",
            confluence_url="",
            engine="gemini",
            target_repo="acme/widgets",
            status="running",
        )
    )

    resp = client.get("/ui/runs/run-in-progress")

    assert resp.status_code == 200
    assert "still in progress" in resp.text


def test_delete_run_removes_it_from_the_list(client):
    store = RunStore(get_settings("testuser").runs_store_path)
    _seed_runs(store)

    resp = client.post("/ui/runs/run-claude-ok/delete", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/runs"
    remaining = [r["run_id"] for r in store.list_runs()]
    assert remaining == ["run-gemini-fail"]


def test_delete_all_runs_clears_everything(client):
    store = RunStore(get_settings("testuser").runs_store_path)
    _seed_runs(store)

    resp = client.post("/ui/runs/delete-all", follow_redirects=False)

    assert resp.status_code == 303
    assert store.list_runs() == []


def test_retrigger_run_forces_a_rerun_of_the_same_page(client, monkeypatch):
    """The route must resolve page_id from the stored run (not trust a
    client-supplied one) and call retrigger_page with it -- see
    ui/routes.py's docstring for why. TestClient runs FastAPI
    BackgroundTasks in-process before returning (same as the webhook/simulate
    flow), so the mock is already called by the time this assertion runs.
    """
    store = RunStore(get_settings("testuser").runs_store_path)
    _seed_runs(store)

    calls = []

    async def _fake_retrigger_page(settings, page_id):
        calls.append((settings.data_dir, page_id))

    monkeypatch.setattr(ui_routes, "retrigger_page", _fake_retrigger_page)

    resp = client.post("/ui/runs/run-claude-ok/retrigger", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/runs"
    assert len(calls) == 1
    assert calls[0][1] == "1"  # run-claude-ok's page_id, from _seed_runs


def test_retrigger_run_is_a_noop_for_an_unknown_run_id(client, monkeypatch):
    calls = []

    async def _fake_retrigger_page(settings, page_id):
        calls.append(page_id)

    monkeypatch.setattr(ui_routes, "retrigger_page", _fake_retrigger_page)

    resp = client.post("/ui/runs/does-not-exist/retrigger", follow_redirects=False)

    assert resp.status_code == 303
    assert calls == []


def test_run_detail_page_shows_full_record_including_usage_and_raw_log(client):
    settings = get_settings("testuser")
    store = RunStore(settings.runs_store_path)
    from confluence_pr_agent.models import RunRecord

    store.add_run(
        RunRecord(
            run_id="detail-1",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00",
            duration_seconds=5.2,
            page_id="42",
            page_title="Some Spec",
            confluence_url="https://example.atlassian.net/wiki/pages/42",
            engine="claude_code",
            target_repo="acme/widgets",
            status="opened_pr",
            files_changed=["appointments/models.py", "appointments/tests/test_models.py"],
            pr_number=9,
            pr_url="https://github.com/acme/widgets/pull/9",
            summary="Added a cancellation reason field.",
            email_sent=True,
            usage={"input_tokens": 1200, "output_tokens": 340, "total_cost_usd": 0.021},
            raw_log="[assistant] Reading models.py\n[tool_use] Edit ...",
            spec_diff="-Patients cannot cancel.\n+Patients can cancel with a reason.",
        )
    )

    resp = client.get("/ui/runs/detail-1")

    assert resp.status_code == 200
    assert "Some Spec" in resp.text
    assert "Added a cancellation reason field." in resp.text
    assert "appointments/models.py" in resp.text
    assert "1,200" in resp.text  # usage summarized into a readable table, not raw JSON
    assert "Input tokens" in resp.text
    assert "Full usage data" in resp.text  # accordion holding the full raw usage dict
    assert "Reading models.py" in resp.text  # raw log present (inside a collapsed accordion)
    assert "Raw engine output" in resp.text
    assert "sent" in resp.text

    # spec diff, colored and visible (not tucked in an accordion -- see the note explaining why)
    assert "Confluence Spec Change" in resp.text
    assert '<span class="diff-remove">-Patients cannot cancel.</span>' in resp.text
    assert '<span class="diff-add">+Patients can cancel with a reason.</span>' in resp.text

    # the flow diagram: all 6 stages shown, every one "done" for a completed opened_pr run
    # (count class="flow-step flow-step--done" specifically -- the bare substring
    # "flow-step--done" also appears once in the <style> block's CSS selector)
    assert "How this was built" in resp.text
    for label in ["Confluence", "Clone Repo", "AI Agent", "Tests", "Pull Request", "Email"]:
        assert label in resp.text
    assert resp.text.count("flow-step flow-step--done") == 6
    assert "flow-step flow-step--pending" not in resp.text


def test_run_detail_page_shows_jira_story_chip_and_overview_row(client):
    settings = get_settings("testuser")
    store = RunStore(settings.runs_store_path)
    from confluence_pr_agent.models import RunRecord

    store.add_run(
        RunRecord(
            run_id="detail-jira", started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00", duration_seconds=5.2,
            page_id="42", page_title="Some Spec", confluence_url="https://example.atlassian.net/wiki/pages/42",
            engine="claude_code", target_repo="acme/widgets", status="opened_pr",
            jira_issue_key="KAN-33", jira_issue_url="https://example.atlassian.net/browse/KAN-33",
            jira_reused=True,
        )
    )

    resp = client.get("/ui/runs/detail-jira")
    assert resp.status_code == 200
    assert resp.text.count("KAN-33") >= 2  # header chip + Overview row
    assert "existing story reused for reference" in resp.text
    assert 'href="https://example.atlassian.net/browse/KAN-33"' in resp.text


def test_run_detail_page_highlights_next_steps_as_a_warning_callout(client):
    settings = get_settings("testuser")
    store = RunStore(settings.runs_store_path)
    from confluence_pr_agent.models import RunRecord

    store.add_run(
        RunRecord(
            run_id="detail-scope-gap", started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:05+00:00", duration_seconds=5.2,
            page_id="42", page_title="Some Spec", confluence_url="https://example.atlassian.net/wiki/pages/42",
            engine="claude_code", target_repo="acme/widgets", status="opened_pr",
            summary=(
                "I implemented the backend change.\n\n"
                "### Next steps\n"
                "Add the `repo-ui` label and re-run the pipeline."
            ),
            flagged_scope_gap=True,
        )
    )

    resp = client.get("/ui/runs/detail-scope-gap")
    assert resp.status_code == 200
    assert "I implemented the backend change." in resp.text
    assert "Add the `repo-ui` label and re-run the pipeline." in resp.text
    assert "Next steps &mdash; more action needed" in resp.text
    # The "### Next steps" heading itself must not leak into the visible body text.
    assert "### Next steps" not in resp.text


def test_run_detail_page_shows_progress_heading_and_active_stage_when_running(client):
    from confluence_pr_agent.models import RunRecord

    store = RunStore(get_settings("testuser").runs_store_path)
    store.upsert_run(
        RunRecord(
            run_id="running-detail",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:00:00+00:00",
            duration_seconds=12.0,
            page_id="99",
            page_title="",
            confluence_url="",
            engine="gemini",
            target_repo="acme/widgets",
            status="running",
            current_stage="ai_agent",
        )
    )

    resp = client.get("/ui/runs/running-detail")

    assert resp.status_code == 200
    assert "Progress" in resp.text
    assert "flow-step flow-step--active" in resp.text
    assert resp.text.count("flow-step flow-step--done") == 3  # fetch_page, create_jira_story, clone_repo
    assert "Confluence Spec Change" not in resp.text  # no spec_diff recorded for this run


def test_run_detail_page_404s_for_unknown_run_id(client):
    resp = client.get("/ui/runs/does-not-exist")
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()


def test_simulate_form_renders(client):
    resp = client.get("/ui/simulate")
    assert resp.status_code == 200
    assert "Simulate Confluence Webhook" in resp.text
    assert "Poll now" in resp.text


def test_poll_trigger_runs_a_poll_cycle_in_the_background_and_redirects(client, monkeypatch):
    calls = []

    async def _fake_poll_once(settings=None):
        calls.append(settings)
        return []

    monkeypatch.setattr(ui_routes, "poll_once", _fake_poll_once)

    resp = client.post("/ui/poll/trigger", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/simulate?polled=1"
    assert len(calls) == 1

    resp = client.get("/ui/simulate?polled=1")
    assert "Poll cycle started" in resp.text


def test_simulate_rejects_missing_page_id(client):
    resp = client.post("/ui/simulate", data={"page_id": ""})
    assert resp.status_code == 400
    assert "required" in resp.text.lower()


def test_simulate_rejects_non_numeric_page_id(client):
    resp = client.post("/ui/simulate", data={"page_id": "not-a-number"})
    assert resp.status_code == 400


def test_simulate_posts_signed_payload_with_correct_headers(client, monkeypatch):
    """Confirms payload construction + signing, by mocking the outbound HTTP
    call itself (ui_routes._post_webhook) rather than the network -- see
    that function's docstring for why it's a real socket call in production
    (not httpx.ASGITransport) and thus not something to fake a live server
    for in a unit test.
    """
    # /ui/simulate always resolves the bootstrap/default user's Settings
    # (see routes.py::simulate_submit) -- which is "testuser" here too,
    # since _isolated_env_file sets DEFAULT_USER=testuser. Settings no
    # longer reads os.environ at all (see config.py), so the secret has to
    # actually be saved through the real config-write path, not
    # monkeypatch.setenv.
    client.post("/ui/config", data={"CONFLUENCE_WEBHOOK_SECRET": "shh"})

    captured = {}

    async def _fake_post_webhook(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"status": "accepted", "page_id": "123456"}, request=request)

    monkeypatch.setattr(ui_routes, "_post_webhook", _fake_post_webhook)

    resp = client.post(
        "/ui/simulate",
        data={"page_id": "123456", "title": "Appointment Spec", "space_key": "SD"},
    )

    assert resp.status_code == 200
    assert "HTTP 200" in resp.text
    assert captured["url"].endswith("/webhook/confluence")

    payload = json.loads(captured["body"])
    assert payload["page"] == {"id": "123456", "title": "Appointment Spec", "spaceKey": "SD"}

    expected_signature = "sha256=" + hmac.new(b"shh", captured["body"], hashlib.sha256).hexdigest()
    assert captured["headers"]["X-Hub-Signature-256"] == expected_signature


def test_simulate_omits_signature_header_when_no_secret_configured(client, monkeypatch):
    captured = {}

    async def _fake_post_webhook(url, body, headers):
        captured["headers"] = headers
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"status": "accepted"}, request=request)

    monkeypatch.setattr(ui_routes, "_post_webhook", _fake_post_webhook)

    resp = client.post("/ui/simulate", data={"page_id": "123456"})

    assert resp.status_code == 200
    assert "X-Hub-Signature-256" not in captured["headers"]
