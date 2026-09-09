"""Browser UI: configure credentials, browse run history, simulate a webhook.

Every /ui/* route requires a Depends(current_username) -- see ui/auth.py --
resolved from the X-Auth-User header Caddy forwards after Basic Auth (see
Caddyfile.example). Everything each user sees/edits (Settings, run history,
page-version tracking) is scoped to that username; see config.py's
get_settings(username) and its module docstring for how that isolation is
enforced.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from pathlib import Path

import httpx
from anthropic import AsyncAnthropic
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from google import genai
from openai import AsyncOpenAI

from confluence_pr_agent.confluence.client import ConfluenceClient
from confluence_pr_agent.config import clear_settings_cache, get_process_config, get_settings
from confluence_pr_agent.jira.client import JiraClient
from confluence_pr_agent.jira.bug_triage import (
    analyze_issue,
    create_fix_run,
    clear_analysis_display,
    fix_issue,
    load_scan,
    poll_jira,
)
from confluence_pr_agent.pipeline.poller import poll_once, retrigger_page
from confluence_pr_agent.pipeline.jira_bug_poller import mark_approved_issues
from confluence_pr_agent.pipeline.stages import STAGE_LABELS
from confluence_pr_agent.repo.github_client import GitHubClient
from confluence_pr_agent.repo.test_command_detection import detect_tech_stack, detect_test_command
from confluence_pr_agent.storage.run_store import RunStore
from confluence_pr_agent.ui.auth import current_username
from confluence_pr_agent.ui.config_fields import (
    ALL_ENGINES,
    CONFIG_FIELDS,
    ENGINE_CREDENTIAL_BY_ENGINE,
    JUDGE_MODEL_OPTIONS_BY_PROVIDER,
    REPO_CREDENTIAL_BY_PROVIDER,
)
from confluence_pr_agent.ui.diff_view import render_diff_html
from confluence_pr_agent.ui.pipeline_flow import build_flow_steps, split_next_steps
from confluence_pr_agent.ui.usage_summary import summarize_usage

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Every status run_pipeline can record -- see models.py::PipelineResult.status
# and orchestrator.py's "running" placeholder. Fixed, not derived from run
# history, for the same reason as ALL_ENGINES: the filter shouldn't be
# limited to outcomes that happen to have occurred yet.
ALL_STATUSES = [
    "running", "opened_pr", "tests_failed", "judge_rejected", "error", "no_change_detected", "ignored",
    "no_repo_matched", "awaiting_approval",
]

STATUS_LABELS = {
    "running": "Running",
    "opened_pr": "Open PR",
    "tests_failed": "Tests Failed",
    "judge_rejected": "Judge Rejected",
    "error": "Error",
    "no_change_detected": "No Change",
    "ignored": "Ignored (label)",
    # A configured repo list, but none of their routing labels matched this
    # page -- see pipeline/orchestrator.py's scope-resolution step. Distinct
    # from "ignored" (that one's the poll-level CONFLUENCE_ALLOWED_LABELS
    # gate, a page never even considered) and from "no_changes" below (a
    # per-repo outcome, not a whole-run one).
    "no_repo_matched": "No Repo Matched",
    # JIRA_APPROVAL_REQUIRED is on and the story hasn't reached
    # jira_approved_status_name yet -- see pipeline/approval_poller.py. The
    # run resumes (a new RunRecord, not this one updated in place) once
    # approval_poller.py detects the status match.
    "awaiting_approval": "Awaiting Approval",
    # Per-repo only (RepoChangeResult.status, not RunRecord.status) -- a
    # repo that was in scope but the agent decided didn't need editing.
    "no_changes": "No Changes",
}


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


# Registered as a Jinja filter (`{{ run.status | status_label }}`) so every
# template that shows a status -- the filter dropdown, table badges, the
# detail page header -- uses the same human-readable text without each one
# needing the mapping threaded into its own route's context.
templates.env.filters["status_label"] = _status_label
templates.env.filters["stage_label"] = lambda stage: STAGE_LABELS.get(stage, stage)


def _is_valid_int(value: str) -> bool:
    try:
        int(value)
        return True
    except ValueError:
        return False


def _write_env_updates(path: Path, updates: dict[str, str]) -> None:
    # Deliberately not python-dotenv's set_key(): it writes via a temp file
    # + os.replace() rename, which raises "Device or resource busy" when
    # .env is bind-mounted as a single file into the container (the rename
    # target is a mount point, not a plain file the kernel will let us
    # replace). Rewriting the existing file's content in place sidesteps
    # that entirely.
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                new_lines.append(f"{key}={remaining.pop(key)}")
                continue
        new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n")


@router.get("/ui")
async def ui_home() -> RedirectResponse:
    return RedirectResponse(url="/ui/runs")


# ---------------------------------------------------------------------------
# How it works -- a walkthrough of the pipeline for live demos, so the
# journey can be narrated step by step inside the app itself instead of a
# separate slide deck. Built from a synthetic, fully-"done" run through
# build_flow_steps() so the overview diagram at the top always matches
# pipeline/stages.py -- the same source of truth /ui/runs/{id} uses -- and
# can't quietly drift out of sync with the real pipeline.
# ---------------------------------------------------------------------------


@router.get("/ui/how-it-works")
async def how_it_works(request: Request, username: str = Depends(current_username)):
    settings = get_settings(username)
    demo_run = {
        "status": "opened_pr",
        "current_stage": "send_email",
        "engine": settings.change_agent_engine,
        "target_repo": settings.target_repo,
        "page_id": "458753",
        "page_title": "Checkout Flow Spec",
        "files_changed": ["src/checkout.py", "tests/test_checkout.py"],
        "pr_number": 42,
        "email_sent": True,
        "email_error": None,
        "judge_verdict": "approved",
        "jira_issue_key": "SD-101",
    }
    return templates.TemplateResponse(
        request,
        "how_it_works.html",
        {
            "flow_steps": build_flow_steps(demo_run),
            "settings": settings,
        },
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _current_env_values(username: str) -> dict[str, str]:
    # Reads through this user's own Settings, not any shared file/os.environ
    # -- CONFIG_FIELDS keys (e.g. "TARGET_REPO") map 1:1 onto Settings'
    # lowercase snake_case attribute names by construction (standard
    # pydantic-settings convention), so f.key.lower() always resolves.
    settings = get_settings(username)
    values: dict[str, str] = {}
    for f in CONFIG_FIELDS:
        value = getattr(settings, f.key.lower(), "")
        if isinstance(value, bool):
            values[f.key] = "true" if value else "false"
        else:
            values[f.key] = str(value) if value else ""
    return values


def _config_context(username: str, saved: bool = False, error: str | None = None) -> dict:
    values = _current_env_values(username)
    # Always seeded from resolved_repo_targets (config.py), not the raw
    # TARGET_REPOS_JSON env value directly -- for a legacy single-repo user
    # who's never touched this field, that property already builds the
    # right one-row fallback from TARGET_REPO/TARGET_REPO_BASE_BRANCH/
    # TARGET_REPO_TEST_COMMAND, so the new repeating-row editor shows their
    # actual current config instead of an empty, "your config vanished"
    # list. Hitting Save with no changes writes that same row back as real
    # TARGET_REPOS_JSON -- a one-time, transparent migration onto the new
    # representation, not a behavior change.
    values["TARGET_REPOS_JSON"] = json.dumps(
        [
            {
                "target_repo": rt.target_repo,
                "base_branch": rt.base_branch,
                "test_command": rt.test_command,
                "label": rt.label,
                "tech_stack": rt.tech_stack,
                "coding_standards": rt.coding_standards,
                "lint_command": rt.lint_command,
            }
            for rt in get_settings(username).resolved_repo_targets
        ]
    )
    current_judge_provider = values.get("JUDGE_PROVIDER", "").lower()
    groups: dict[str, list[dict]] = {}
    for f in CONFIG_FIELDS:
        current = values.get(f.key, "")
        # JUDGE_MODEL's option list depends on JUDGE_PROVIDER (each provider
        # has its own model names) -- this seeds the right list for the
        # user's *current* provider on first paint; config.html's JS swaps
        # it live if they change the provider dropdown without reloading.
        if f.key == "JUDGE_MODEL":
            f = replace(
                f, options=JUDGE_MODEL_OPTIONS_BY_PROVIDER.get(current_judge_provider, f.options)
            )
        groups.setdefault(f.group, []).append(
            {
                "field": f,
                "display_value": "" if f.secret else current,
                "is_set": bool(current),
            }
        )
    return {
        "groups": groups,
        "saved": saved,
        "error": error,
        "engine_credential_by_engine": ENGINE_CREDENTIAL_BY_ENGINE,
        "repo_credential_by_provider": REPO_CREDENTIAL_BY_PROVIDER,
        "judge_model_options_by_provider": JUDGE_MODEL_OPTIONS_BY_PROVIDER,
    }


@router.get("/ui/config")
async def config_form(request: Request, username: str = Depends(current_username)):
    return templates.TemplateResponse(request, "config.html", _config_context(username))


@router.get("/ui/jira-bugs")
async def jira_bugs(request: Request, username: str = Depends(current_username)):
    settings = get_settings(username)
    analysis_issue_key = request.cookies.get("jira_analysis_once")
    if not analysis_issue_key:
        clear_analysis_display(settings)
    scan = load_scan(settings)
    response = templates.TemplateResponse(request, "jira_bugs.html", {"scan": scan, "settings": settings})
    if analysis_issue_key:
        response.delete_cookie("jira_analysis_once")
    return response


@router.post("/ui/jira-bugs/poll")
async def jira_bugs_poll(request: Request, username: str = Depends(current_username)):
    try:
        settings = get_settings(username)
        scan = await poll_jira(settings)
        mark_approved_issues(settings, scan)
        return RedirectResponse(url="/ui/jira-bugs?polled=1", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/ui/jira-bugs?error={str(exc)[:180]}", status_code=303)


@router.post("/ui/jira-bugs/{issue_key}/analyze")
async def jira_bug_analyze(issue_key: str, username: str = Depends(current_username)):
    try:
        await analyze_issue(get_settings(username), issue_key)
        response = RedirectResponse(url="/ui/jira-bugs?analyzed=1", status_code=303)
        response.set_cookie("jira_analysis_once", issue_key, httponly=True, samesite="lax")
        return response
    except Exception as exc:
        return RedirectResponse(url=f"/ui/jira-bugs?error={str(exc)[:180]}", status_code=303)


@router.post("/ui/jira-bugs/{issue_key}/fix")
async def jira_bug_fix(issue_key: str, background_tasks: BackgroundTasks, username: str = Depends(current_username)):
    try:
        settings = get_settings(username)
        scan = load_scan(settings)
        issue = next((item for item in scan["issues"] if item["key"] == issue_key), None)
        if not issue:
            raise ValueError("Issue is not in the latest Jira scan")
        if issue.get("triage") not in {"analyzed", "approved"}:
            raise ValueError("Analyze the bug and wait for developer approval before starting the agent")
        approved_name = settings.jira_approved_status_name.strip().lower()
        if not settings.jira_approval_required or not approved_name:
            raise ValueError("Configure Jira approval and an approved status before starting the agent")
        if issue.get("status", "").strip().lower() != approved_name:
            raise ValueError(f"The developer must move this bug to {settings.jira_approved_status_name!r} first")
        run_id = create_fix_run(settings, issue)
        background_tasks.add_task(fix_issue, settings, issue_key, run_id)
        return RedirectResponse(url=f"/ui/jira-bugs?started=1&run_id={run_id}", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/ui/jira-bugs?error={str(exc)[:180]}", status_code=303)


@router.post("/ui/config")
async def save_config(request: Request, username: str = Depends(current_username)):
    form = await request.form()

    # Written only to this user's own .env file -- NOT os.environ. With
    # multiple users' Settings potentially live in this one process,
    # writing to process-wide os.environ here would clobber whatever any
    # other concurrently-running user's change-engine subprocess reads next
    # (see agent/engines/claude_code.py etc. for why those now take an
    # explicit api_key instead of relying on ambient env). The .env file is
    # what survives a container restart -- but only if it's bind-mounted
    # rather than passed via `--env-file` at container creation (env-file
    # only sets process env, it doesn't put a file on the container's
    # filesystem). See SETUP.md.
    env_path = get_process_config().user_env_path(username)
    if not env_path.exists():
        env_path.write_text("")

    updates: dict[str, str] = {}
    for f in CONFIG_FIELDS:
        raw = form.get(f.key)
        if raw is None:
            continue
        value = str(raw).strip()
        if f.secret and value == "":
            continue  # blank secret field means "leave unchanged" -- we never redisplay it
        if f.input_type == "number" and (value == "" or not _is_valid_int(value)):
            # A blank/non-numeric value here has no valid meaning (unlike a
            # blank text field) -- writing it would corrupt .env into
            # something Settings() can't parse at all, breaking every route
            # that calls get_settings() until someone notices and fixes the
            # file by hand. "Leave unchanged" is the only safe fallback.
            continue
        updates[f.key] = value

    if updates:
        _write_env_updates(env_path, updates)

    clear_settings_cache()
    return RedirectResponse(url="/ui/config?saved=1", status_code=303)


@router.get("/ui/repos/detect-test-command")
async def detect_test_command_route(repo: str, username: str = Depends(current_username)):
    """Called by the repeating repo-config editor's JS when a row's repo
    field is filled in -- looks at that repo's actual root files (via this
    user's own GitHub token) and suggests a test command instead of leaving
    it blank or wrong. Best-effort: any failure (bad repo name, no access,
    network) degrades to {"test_command": None, "tech_stack": None}, same
    fail-open idiom as everything else here -- this is a convenience, not
    something that should be able to break the config page.

    Also suggests a tech_stack (RepoTarget.tech_stack) from the same root
    file listing -- one GitHub call covers both detections, since they're
    reading the same marker files for different purposes.
    """
    settings = get_settings(username)
    repo = repo.strip()
    if not repo or not settings.github_token:
        return {"test_command": None, "tech_stack": None}

    github = GitHubClient(settings.github_token)
    try:
        files = await github.list_root_files(repo)
        return {"test_command": detect_test_command(files), "tech_stack": detect_tech_stack(files)}
    except Exception:
        return {"test_command": None, "tech_stack": None}
    finally:
        await github.aclose()


def _http_error_message(exc: httpx.HTTPStatusError) -> str:
    status = exc.response.status_code
    if status == 401:
        return "Authentication failed — check the credentials."
    if status == 403:
        # Distinct from 401 on purpose -- Atlassian returns this when the
        # email/token pair is genuinely valid (auth succeeded) but the
        # account has no product access grant on this site (e.g. a Jira
        # seat but no Confluence one, or vice versa) -- confirmed live
        # against a real "correct" token that failed with exactly this.
        # Telling someone to "check the credentials" here sends them
        # chasing a token rotation that was never the problem.
        return "Credentials are valid, but this account doesn't have access to this product on this site — check product access in Atlassian admin, not the token."
    if status == 404:
        return "Not found — check the URL/key."
    return f"Request failed (HTTP {status})."


def _sdk_error_message(exc: Exception) -> str:
    # Anthropic/OpenAI's SDKs raise their own APIStatusError (.status_code);
    # Gemini's raises google.genai.errors.APIError (.code) -- and, per the
    # log that prompted this endpoint, Gemini reports an invalid key as a
    # 400 INVALID_ARGUMENT, not a 401/403, so 400 is grouped in with them
    # here specifically for that reason, not as a generic "bad request".
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in (400, 401, 403):
        return "Authentication failed — check the API key."
    if status:
        return f"Request failed (HTTP {status})."
    return f"Couldn't reach the provider: {exc}"


# Cheapest authenticated call each SDK offers -- listing models costs
# nothing and needs no prompt, same "cheapest authenticated call" idiom as
# the Confluence/Jira/GitHub clients' own test_connection() methods.
async def _test_llm_key(service: str, api_key: str) -> None:
    if service == "anthropic":
        await AsyncAnthropic(api_key=api_key).models.list(limit=1)
    elif service == "openai":
        await AsyncOpenAI(api_key=api_key).models.list()
    elif service == "gemini":
        await genai.Client(api_key=api_key).aio.models.list()


@router.post("/ui/test-connection/{service}")
async def test_connection(service: str, request: Request, username: str = Depends(current_username)):
    """Validates a set of credentials live -- called by each tab's "Test
    connection" button before Save, so a typo'd token or wrong base URL
    surfaces here instead of silently failing deep into a real pipeline run.

    Reads values from the request body (the page's current, possibly
    unsaved form fields), not this user's stored Settings, so the test
    reflects exactly what's about to be saved -- except a blank secret
    field, which falls back to the already-saved value, same "blank means
    unchanged" semantics POST /ui/config uses (a secret field is never
    redisplayed, so JS can't send back what it can't read).
    """
    settings = get_settings(username)
    payload = await request.json()

    def value(field_key: str, settings_key: str) -> str:
        raw = str(payload.get(field_key) or "").strip()
        return raw or str(getattr(settings, settings_key, "") or "")

    try:
        if service == "confluence":
            base_url = value("base_url", "confluence_base_url")
            email = value("email", "confluence_user_email")
            token = value("api_token", "confluence_api_token")
            if not (base_url and email and token):
                return {"ok": False, "message": "Base URL, account email, and API token are all required."}
            client = ConfluenceClient(base_url, email, token)
            try:
                name = await client.test_connection()
            finally:
                await client.aclose()
            return {"ok": True, "message": f"Connected as {name}." if name else "Connected."}

        if service == "jira":
            base_url = value("base_url", "jira_base_url")
            email = value("email", "jira_user_email")
            token = value("api_token", "jira_api_token")
            if not (base_url and email and token):
                return {"ok": False, "message": "Site URL, account email, and API token are all required."}
            client = JiraClient(base_url, email, token)
            try:
                name = await client.test_connection()
            finally:
                await client.aclose()
            return {"ok": True, "message": f"Connected as {name}." if name else "Connected."}

        if service == "github":
            token = value("token", "github_token")
            if not token:
                return {"ok": False, "message": "GitHub PAT is required."}
            client = GitHubClient(token)
            try:
                login = await client.test_connection()
            finally:
                await client.aclose()
            return {"ok": True, "message": f"Connected as {login}." if login else "Connected."}

        # Shared by the Change Engine tab (claude_code/codex/gemini all use
        # this same key) and the LLM Judge tab (JUDGE_PROVIDER reuses these
        # same three keys rather than having its own -- see config_fields.py)
        # -- one test per credential, not one per role that credential plays.
        if service in ("anthropic", "openai", "gemini"):
            settings_key = f"{service}_api_key"
            api_key = value("api_key", settings_key)
            if not api_key:
                return {"ok": False, "message": "API key is required."}
            await _test_llm_key(service, api_key)
            return {"ok": True, "message": "Connected."}

        if service == "cursor":
            return {
                "ok": None,
                "message": "Cursor has no directly testable API from here — the CLI validates its own key when it actually runs.",
            }

        return {"ok": False, "message": f"Unknown service: {service}"}

    except httpx.HTTPStatusError as exc:
        return {"ok": False, "message": _http_error_message(exc)}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Couldn't reach the server: {exc}"}
    except Exception as exc:
        return {"ok": False, "message": _sdk_error_message(exc)}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def _filter_runs(runs: list[dict], *, engine: str, status: str, date_from: str, date_to: str) -> list[dict]:
    filtered = runs
    if engine:
        filtered = [r for r in filtered if r.get("engine") == engine]
    if status:
        filtered = [r for r in filtered if r.get("status") == status]
    if date_from:
        # started_at is a full ISO timestamp; date_from/date_to are bare
        # "YYYY-MM-DD" values from an <input type="date">. Comparing the
        # date prefix as a string works because ISO dates sort lexically.
        filtered = [r for r in filtered if r.get("started_at", "")[:10] >= date_from]
    if date_to:
        filtered = [r for r in filtered if r.get("started_at", "")[:10] <= date_to]
    return filtered


@router.get("/ui/runs")
async def runs_list(
    request: Request,
    engine: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    username: str = Depends(current_username),
):
    settings = get_settings(username)
    store = RunStore(settings.runs_store_path)
    all_runs = store.list_runs()

    runs = _filter_runs(all_runs, engine=engine, status=status, date_from=date_from, date_to=date_to)

    # "Require approval before implementation" (Jira tab, Config) -- when on,
    # a poll/webhook creates an `awaiting_approval` run as a placeholder the
    # moment a Jira story is filed, before any real implementation work has
    # happened. Hidden from the default view (still reachable via the Status
    # filter) so this page reads as "what actually got implemented", not a
    # queue of stories still waiting on a human -- unless the Status filter
    # was explicitly set to awaiting_approval, in which case that's exactly
    # what was asked for.
    hide_awaiting_approval = settings.jira_approval_required and status != "awaiting_approval"
    visible_runs = [r for r in runs if r.get("status") != "awaiting_approval"] if hide_awaiting_approval else runs
    hidden_count = len(runs) - len(visible_runs)

    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "runs": visible_runs,
            "total_count": len(all_runs),
            "filtered_count": len(runs),
            "hidden_count": hidden_count,
            "filters": {"engine": engine, "status": status, "date_from": date_from, "date_to": date_to},
            "engines_available": ALL_ENGINES,
            "statuses_available": ALL_STATUSES,
            "any_filter_active": bool(engine or status or date_from or date_to),
        },
    )


@router.post("/ui/runs/delete-all")
async def delete_all_runs(username: str = Depends(current_username)):
    settings = get_settings(username)
    RunStore(settings.runs_store_path).clear_all()
    return RedirectResponse(url="/ui/runs", status_code=303)


@router.post("/ui/runs/{run_id}/delete")
async def delete_run(run_id: str, username: str = Depends(current_username)):
    settings = get_settings(username)
    RunStore(settings.runs_store_path).delete_run(run_id)
    return RedirectResponse(url="/ui/runs", status_code=303)


@router.post("/ui/runs/{run_id}/retrigger")
async def retrigger_run(run_id: str, background_tasks: BackgroundTasks, username: str = Depends(current_username)):
    """Force-reprocesses the same Confluence page a past run touched, even
    if nothing textually changed since -- see poller.py::retrigger_page.
    Looks the page_id up from the past run rather than taking it as a form
    field, so this can't be pointed at an arbitrary page_id by a crafted
    request. Backgrounded like every other real trigger here (poll, "Poll
    now", the webhook) -- a real agentic engine can take minutes.
    """
    settings = get_settings(username)
    run = RunStore(settings.runs_store_path).get_run(run_id)
    if run and run.get("page_id"):
        background_tasks.add_task(retrigger_page, settings, run["page_id"])
    return RedirectResponse(url="/ui/runs", status_code=303)


@router.get("/ui/runs/{run_id}")
async def run_detail(request: Request, run_id: str, username: str = Depends(current_username)):
    settings = get_settings(username)
    store = RunStore(settings.runs_store_path)
    run = store.get_run(run_id)
    if run is None:
        return templates.TemplateResponse(
            request, "run_detail.html", {"run": None, "run_id": run_id}, status_code=404
        )
    spec_diff = run.get("spec_diff")
    summary_body, summary_next_steps = split_next_steps(run["summary"]) if run.get("summary") else (None, None)
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run": run,
            "run_id": run_id,
            "usage_summary": summarize_usage(run.get("usage")),
            "flow_steps": build_flow_steps(run),
            "spec_diff_html": render_diff_html(spec_diff) if spec_diff else None,
            "summary_body": summary_body,
            "summary_next_steps": summary_next_steps,
        },
    )


# ---------------------------------------------------------------------------
# Webhook simulator
# ---------------------------------------------------------------------------


def _build_webhook_payload(page_id: str, title: str, space_key: str) -> dict:
    page: dict[str, str] = {"id": page_id}
    if title:
        page["title"] = title
    if space_key:
        page["spaceKey"] = space_key
    return {"event": "page_updated", "page": page, "timestamp": int(time.time() * 1000)}


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def _post_webhook(url: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
    """A real, socket-based HTTP call -- deliberately not httpx.ASGITransport.

    ASGITransport dispatches in-process with no real socket to detach from,
    so Starlette's BackgroundTasks (attached to the /webhook/confluence
    response) run to completion *before* this call returns -- meaning the
    /ui/simulate request would block for the entire pipeline run (confirmed:
    minutes, for a real agentic engine) instead of returning immediately
    like a real webhook delivery does. A genuine socket round-trip doesn't
    have that problem: the response is flushed once the route handler
    returns, and the background task keeps running server-side after that,
    same as a real Confluence-to-us webhook call.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.post(url, content=body, headers=headers)


@router.get("/ui/simulate")
async def simulate_form(request: Request, username: str = Depends(current_username)):
    # Two different Settings deliberately: the "Poll now" section acts on
    # (and displays) the REQUESTING user's own config, since /ui/poll/trigger
    # is genuinely per-user -- but "simulate a webhook delivery" round-trips
    # through the real, single global /webhook/confluence endpoint (see
    # simulate_submit and _post_webhook's docstring for why it's a real HTTP
    # call, not an in-process one), which has no way to know which of
    # several users a request is "for" and always resolves that one fixed
    # bootstrap identity instead. See simulate.html for the note explaining
    # this distinction to whoever's looking at the page.
    poll_settings = get_settings(username)
    webhook_settings = get_settings(get_process_config().resolved_default_user)
    return templates.TemplateResponse(
        request,
        "simulate.html",
        {
            "poll_space_key": poll_settings.confluence_space_key,
            "poll_labels": poll_settings.confluence_allowed_labels_list,
            "default_space_key": webhook_settings.confluence_space_key,
            "polled": request.query_params.get("polled") == "1",
            "prefill": None,
            "result": None,
            "error": None,
        },
    )


@router.post("/ui/poll/trigger")
async def trigger_poll(background_tasks: BackgroundTasks, username: str = Depends(current_username)):
    """The "on demand" counterpart to the real per-user poll loop
    (pipeline/poller.py, started from the app's lifespan) -- same
    poll_once() call, just fired immediately for THIS user instead of
    waiting out their own CONFLUENCE_POLL_INTERVAL_SECONDS. Unlike
    /ui/simulate below, this one genuinely is per-user: it calls
    build_deps/run_pipeline directly rather than round-tripping through the
    single global /webhook/confluence endpoint. Backgrounded like the real
    webhook route, since a poll that finds several pages can take minutes.
    """
    settings = get_settings(username)
    background_tasks.add_task(poll_once, settings)
    return RedirectResponse(url="/ui/simulate?polled=1", status_code=303)


@router.post("/ui/simulate")
async def simulate_submit(request: Request, username: str = Depends(current_username)):
    form = await request.form()
    # See simulate_form's comment: always the bootstrap user, since this
    # submits to the real, single global /webhook/confluence endpoint.
    settings = get_settings(get_process_config().resolved_default_user)

    page_id = str(form.get("page_id") or "").strip()
    title = str(form.get("title") or "").strip()
    space_key = str(form.get("space_key") or "").strip()
    prefill = {"page_id": page_id, "title": title, "space_key": space_key}

    if not page_id or not page_id.isdigit():
        return templates.TemplateResponse(
            request,
            "simulate.html",
            {
                "default_space_key": settings.confluence_space_key,
                "poll_labels": settings.confluence_allowed_labels_list,
                "polled": False,
                "prefill": prefill,
                "result": None,
                "error": "Page ID is required and must be numeric -- it's the number in the page's Confluence URL.",
            },
            status_code=400,
        )

    payload = _build_webhook_payload(page_id, title, space_key or settings.confluence_space_key)
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.confluence_webhook_secret:
        headers["X-Hub-Signature-256"] = _sign(settings.confluence_webhook_secret, body)

    # Derived from the incoming request rather than settings.webhook_port --
    # guaranteed to match whatever host:port this server is actually bound
    # to (the container always exposes 8000; a bare local `uvicorn --port X`
    # run could differ from the .env default).
    webhook_url = str(request.base_url).rstrip("/") + "/webhook/confluence"
    try:
        resp = await _post_webhook(webhook_url, body, headers)
        result = {"ok": resp.status_code == 200, "status_code": resp.status_code, "body": resp.text}
    except httpx.HTTPError as exc:
        result = {"ok": False, "status_code": None, "body": str(exc)}

    return templates.TemplateResponse(
        request,
        "simulate.html",
        {
            "default_space_key": settings.confluence_space_key,
            "poll_labels": settings.confluence_allowed_labels_list,
            "polled": False,
            "prefill": prefill,
            "result": result,
            "payload_sent": json.dumps(payload, indent=2),
            "error": None,
        },
    )
