"""Configuration, in two tiers.

`ProcessConfig` is read once at import from the real process environment /
the container's own top-level `.env` -- DATA_DIR (base directory), LOG_LEVEL,
INTERNAL_SHARED_SECRET (checked against the header Caddy forwards after
Basic Auth -- see ui/routes.py::current_username and Caddyfile.example),
DEFAULT_USER (the single-tenant bootstrap identity `/webhook/confluence`
resolves to, and a local `uvicorn --reload` dev fallback when there's no
Caddy in front to forward a real identity). Everything here is genuinely
process-wide; there's only one running server.

`Settings` is everything else (Confluence, repo, change engine, judge,
Jira, email, polling) -- one instance PER AUTHENTICATED USER (see
get_settings below), loaded from that user's own
`data/users/<username>/.env` and nothing else. `Settings` deliberately
never reads os.environ at all (see settings_customise_sources) -- with
N users' Settings potentially constructed in the same process, falling
back to process-wide os.environ would silently leak one user's values into
another's. This is the same bug class this project hit once already (a
real .env value leaked into pytest's Settings() construction mid-session,
silently breaking unrelated tests) before being isolated at the test level
with monkeypatch.delenv; excluding the env source at the model level fixes
it permanently for every caller, not just tests.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from confluence_pr_agent.models import RepoTarget

logger = logging.getLogger(__name__)

DEFAULT_USER_FALLBACK = "default"  # used when DEFAULT_USER is unset, so the webhook path never hard-fails


class ProcessConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: str = Field(default="./data")
    log_level: str = Field(default="INFO")
    webhook_host: str = Field(default="0.0.0.0")
    webhook_port: int = Field(default=8000)
    internal_shared_secret: str = Field(default="")
    default_user: str = Field(default="")

    @property
    def base_data_dir_path(self) -> Path:
        path = Path(self.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def users_dir_path(self) -> Path:
        path = self.base_data_dir_path / "users"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def user_dir_path(self, username: str) -> Path:
        """Creating this on every call is what auto-provisions a brand new
        username the first time anything asks for their directory -- no
        separate "create user" step beyond adding them to Caddy's
        basic_auth. The .env file inside it is created lazily by
        ui/routes.py::save_config on first save, same as today's
        single-tenant behavior.
        """
        path = self.users_dir_path / username
        path.mkdir(parents=True, exist_ok=True)
        return path

    def user_env_path(self, username: str) -> Path:
        return self.user_dir_path(username) / ".env"

    @property
    def resolved_default_user(self) -> str:
        return self.default_user.strip() or DEFAULT_USER_FALLBACK


@lru_cache
def get_process_config() -> ProcessConfig:
    return ProcessConfig()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # No env_settings -- see module docstring. init_settings still wins
        # (get_settings() below always passes _env_file/data_dir
        # explicitly), then this user's own dotenv file, then hardcoded
        # field defaults. Never ambient os.environ.
        return (init_settings, dotenv_settings, file_secret_settings)

    # Confluence
    confluence_base_url: str = Field(default="https://example.atlassian.net/wiki")
    confluence_space_key: str = Field(default="SD")
    confluence_user_email: str = Field(default="")
    confluence_api_token: str = Field(default="")
    confluence_webhook_secret: str = Field(default="")
    # Comma-separated Confluence labels. A page must have at least one of
    # these to be processed -- e.g. "brd,spec-for-agent" so other project
    # content (meeting notes, design docs) in the same space is ignored.
    # Empty = no filtering (every page is eligible), for backward compat.
    confluence_allowed_labels: str = Field(default="")

    # Polling -- Confluence Cloud has no self-service way to register an
    # arbitrary webhook URL (that requires building a Connect/Forge app), so
    # this is the actual trigger mechanism: on a timer, search the space via
    # CQL for pages carrying CONFLUENCE_ALLOWED_LABELS (or every page, if
    # that's blank) and run the pipeline for whichever come back. See
    # pipeline/poller.py. /webhook/confluence still exists and works if
    # something is ever wired to call it, but nothing here relies on it.
    confluence_poll_enabled: bool = Field(default=False)
    confluence_poll_interval_seconds: int = Field(default=300)

    # Target repo -- REPO_PROVIDER is forward-looking config surface only:
    # github is the only value the pipeline actually implements (see
    # pipeline/orchestrator.py::build_deps, which raises if it's anything
    # else). azure_devops/gitlab/bitbucket are listed so the shape of "more
    # than one provider" exists in the UI/settings without pretending any of
    # them work yet -- wiring a second provider is a real client (mirroring
    # repo/git_client.py + repo/github_client.py), not a config toggle.
    repo_provider: str = Field(default="github")
    github_token: str = Field(default="")
    # owner/name (not a URL) -- github_client.py's REST calls and
    # git_client.py's clone URL both build directly off this shape. Revisit
    # the format if/when a second provider is actually implemented; ADO's
    # own addressing (org/project/repo) wouldn't fit this field either way,
    # so generalizing the format now, before there's a second real
    # consumer, would be guessing at a shape rather than deriving one.
    target_repo: str = Field(default="your-org/your-repo")
    target_repo_base_branch: str = Field(default="main")
    target_repo_test_command: str = Field(default="pytest")
    # Real multi-repo config -- a JSON array of {target_repo, base_branch,
    # test_command, label} objects, e.g. for a change that needs coordinated
    # edits across several coupled repos (see pipeline/orchestrator.py's
    # scope-resolution step). Blank (the default) means "not configured" --
    # resolved_repo_targets below then falls back to the single TARGET_REPO/
    # TARGET_REPO_BASE_BRANCH/TARGET_REPO_TEST_COMMAND fields above, so an
    # existing single-repo user's .env needs no changes at all to keep
    # working exactly as before. No UI for editing this yet (still a raw
    # JSON blob) -- see the plan's repeating-repo-config-editor follow-up.
    target_repos_json: str = Field(default="")

    # Global fallback conventions applied to every repo that doesn't set its
    # own RepoTarget.coding_standards -- see agent/prompts.py. Free text:
    # naming, layout, patterns to prefer/avoid.
    coding_standards: str = Field(default="")
    # Confluence page id of a pinned, org-wide engineering-standards page,
    # fetched once per run (ConfluenceClient.fetch_page -- no new client
    # method needed) and folded into the same prompt section as
    # coding_standards above. Blank = not configured, skipped entirely.
    standards_confluence_page_id: str = Field(default="")

    # Change engine (code-writing backend) -- one of:
    # claude_code | cursor | copilot | codex | gemini | antigravity
    # Deliberately per-user like everything else here, even though the
    # engine's CLI binary is a single deployment-wide choice baked into the
    # image at build time (--build-arg CHANGE_AGENT_ENGINE=...) -- a user
    # picking a value the image doesn't have installed just fails at
    # subprocess-spawn time with a clear "not found" error (see each
    # engine's own PATH check), same as it would today.
    change_agent_engine: str = Field(default="claude_code")
    change_agent_max_turns: int = Field(default=30)
    # Self-correction loop: if TARGET_REPO_TEST_COMMAND fails, the change
    # engine gets another attempt with the failure output fed back into its
    # prompt, up to this many attempts total (1 = no retry, today's
    # behavior). See pipeline/orchestrator.py.
    change_agent_max_attempts: int = Field(default=3)
    anthropic_api_key: str = Field(default="")  # claude_code engine + llm judge (see below)
    cursor_api_key: str = Field(default="")  # cursor engine
    openai_api_key: str = Field(default="")  # codex engine
    gemini_api_key: str = Field(default="")  # gemini engine
    kiro_api_key: str = Field(default="")  # kiro engine
    # copilot engine reuses github_token above; antigravity is OAuth-only (no key)

    # LLM-as-judge review gate: after tests pass but before a PR is opened,
    # an independent LLM call reviews the actual code diff against the spec
    # change and can still reject it -- a green test suite doesn't prove the
    # spec was implemented (or implemented without scope creep). Independent
    # of CHANGE_AGENT_ENGINE by design -- see judge/factory.py. If the
    # selected provider's API key isn't set, this step is skipped rather
    # than blocking the PR (judge/factory.py::judge_configured).
    judge_enabled: bool = Field(default=True)
    judge_provider: str = Field(default="anthropic")  # anthropic | openai
    judge_model: str = Field(default="")  # blank = provider's own default (see judge/providers/*)

    # Jira -- optional. Tracks each spec change as a Jira story: created (or,
    # if the story from a previous still-open run for this page is still
    # open, commented on instead of duplicated) once a real change is
    # confirmed, then commented on again with either the PR link or an
    # explanation of what went wrong. Prefers judge_provider/judge_model for
    # the LLM call that writes the story's description/AC -- see
    # jira/story_writer.py -- falling back to gemini_api_key independently
    # of judge_provider (the judge is optional and often unconfigured/
    # broken; the change engine's own key is far more likely to actually
    # work), then a plain-text summary if neither is available.
    # Every Jira call is fail-open: an outage or missing config never blocks
    # or fails the pipeline, same as email/labels/the judge.
    jira_enabled: bool = Field(default=False)
    jira_base_url: str = Field(default="")  # site root, e.g. https://your-team.atlassian.net (no /wiki)
    jira_user_email: str = Field(default="")
    jira_api_token: str = Field(default="")
    jira_project_key: str = Field(default="")
    jira_issue_type: str = Field(default="Story")
    jira_bug_poll_enabled: bool = Field(default=False)
    jira_bug_poll_interval_seconds: int = Field(default=300)
    jira_bug_jql: str = Field(default="")
    jira_poll_limit: int = Field(default=25)
    # Comment-only, never written to the real Story Points field -- that
    # field is a per-instance custom field (customfield_NNNNN) with no
    # stable name, and an LLM's number has no basis in a team's own velocity
    # calibration. A suggestion a human confirms is safer than a number
    # that silently enters real sprint math.
    jira_suggest_story_points: bool = Field(default=False)
    # HITL gate: opt-in, default OFF. When on, run_pipeline stops right
    # after creating/updating the Jira story instead of continuing straight
    # into cloning + implementation -- see pipeline/orchestrator.py's
    # _implement_change split and pipeline/approval_poller.py. Off by
    # default so every existing single-page flow (webhook or poll-triggered)
    # keeps today's fully-automatic behavior unchanged.
    jira_approval_required: bool = Field(default=False)
    # The literal Jira workflow status name (e.g. "Approved") that means "a
    # human approved this for implementation" -- deliberately a status NAME,
    # not JiraIssueStatus.status_category (models.py), which is only the
    # coarse new/indeterminate/done bucket and can't distinguish "approved"
    # from any other in-progress status. Required for jira_approval_required
    # to do anything; matched case-insensitively.
    jira_approved_status_name: str = Field(default="")

    # Team notification email -- one of: sendgrid | postmark
    email_provider: str = Field(default="sendgrid")
    sendgrid_api_key: str = Field(default="")
    postmark_api_key: str = Field(default="")  # Postmark calls this the "Server Token"
    email_from_address: str = Field(default="confluence-pr-agent@example.com")
    email_to_addresses: str = Field(default="team@example.com")

    # Always overridden by get_settings() below to this user's own
    # directory -- present as a field (rather than computed purely outside
    # the model) only so data_dir_path/workdirs_path/page_store_path/
    # runs_store_path keep working unchanged. A value in this user's own
    # .env can't redirect it elsewhere: init_settings (get_settings' kwarg)
    # outranks dotenv_settings in settings_customise_sources above.
    data_dir: str = Field(default="./data")

    @property
    def email_to_list(self) -> list[str]:
        return [addr.strip() for addr in self.email_to_addresses.split(",") if addr.strip()]

    @property
    def confluence_allowed_labels_list(self) -> list[str]:
        return [label.strip().lower() for label in self.confluence_allowed_labels.split(",") if label.strip()]

    @property
    def resolved_repo_targets(self) -> list[RepoTarget]:
        """The repo(s) a spec change can be routed to -- always at least
        one entry (never empty), so callers never need a separate
        single-repo-vs-multi-repo branch of their own.

        Real multi-repo config (TARGET_REPOS_JSON) wins if it's set and
        parses to at least one entry. A malformed value fails open to the
        single-repo fallback rather than raising -- a typo in a raw JSON
        field regressing to "not configured" is far better than every
        pipeline run for this user crashing outright. Logged either way so
        a silent typo doesn't stay invisible forever.
        """
        raw = self.target_repos_json.strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("TARGET_REPOS_JSON must be a JSON array")
                targets = [
                    RepoTarget(
                        target_repo=str(entry["target_repo"]),
                        base_branch=str(entry.get("base_branch") or "main"),
                        test_command=str(entry.get("test_command") or "pytest"),
                        label=str(entry.get("label") or "").strip(),
                        tech_stack=str(entry.get("tech_stack") or "").strip(),
                        coding_standards=str(entry.get("coding_standards") or "").strip(),
                        lint_command=str(entry.get("lint_command") or "").strip(),
                    )
                    for entry in parsed
                ]
                if targets:
                    return targets
                logger.warning("TARGET_REPOS_JSON is an empty list; falling back to TARGET_REPO.")
            except Exception as exc:
                logger.warning("Could not parse TARGET_REPOS_JSON (%s); falling back to TARGET_REPO.", exc)

        # Fallback: today's single-repo fields, as one synthetic entry with
        # no label -- an empty label matches every page (see RepoTarget's
        # docstring), so this behaves exactly like the pre-multi-repo
        # pipeline for every existing user's unmodified .env.
        return [
            RepoTarget(
                target_repo=self.target_repo,
                base_branch=self.target_repo_base_branch,
                test_command=self.target_repo_test_command,
                label="",
            )
        ]

    @property
    def data_dir_path(self) -> Path:
        path = Path(self.data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def workdirs_path(self) -> Path:
        path = self.data_dir_path / "workdirs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def page_store_path(self) -> Path:
        return self.data_dir_path / "page_store.json"

    @property
    def runs_store_path(self) -> Path:
        return self.data_dir_path / "runs.json"

    @property
    def pending_approvals_store_path(self) -> Path:
        return self.data_dir_path / "pending_approvals.json"

    @property
    def sprint_plan_store_path(self) -> Path:
        return self.data_dir_path / "sprint_plans.json"


@lru_cache
def get_settings(username: str) -> Settings:
    process = get_process_config()
    user_dir = process.user_dir_path(username)
    user_env = user_dir / ".env"

    # Preserve the existing single-user setup: the configured default user
    # starts from the mounted top-level .env, while additional identities
    # still receive isolated, hardcoded defaults until they configure them.
    if username == process.resolved_default_user and not user_env.exists():
        root_env = Path(".env")
        if root_env.exists():
            user_env.write_bytes(root_env.read_bytes())

    return Settings(_env_file=str(user_env), data_dir=str(user_dir))


def clear_settings_cache() -> None:
    """Invalidates every cached per-user Settings, not just one user's --
    matches today's single-tenant get_settings.cache_clear() behavior.
    Harmless: the next request for any user just rebuilds their own Settings
    from their own file.
    """
    get_settings.cache_clear()
