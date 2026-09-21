"""Declarative list of every .env field the config UI edits.

Single source of truth for both rendering the form (GET /ui/config) and
processing the submission (POST /ui/config) -- see ui/routes.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Every engine the pipeline supports, regardless of whether any have run yet
# -- the single source of truth for both the config form's dropdown and the
# /ui/runs filter dropdown (which must not be limited to engines that
# happen to already appear in run history).
ALL_ENGINES = ["claude_code", "cursor", "copilot", "codex", "gemini", "antigravity", "kiro"]

# "github" is the only one the pipeline actually implements today (see
# config.py::repo_provider and pipeline/orchestrator.py::build_deps, which
# refuses to run for anything else) -- the rest exist in the dropdown so the
# config surface for "more than one provider" is there without pretending
# any of them are wired up yet.
REPO_PROVIDERS = ["github", "azure_devops", "gitlab", "bitbucket"]

# Keys whose current value is never redisplayed in the form. Submitting one
# blank means "leave it unchanged", not "clear it" -- see routes.py.
ENGINE_CREDENTIAL_KEYS = (
    "ANTHROPIC_API_KEY",
    "CURSOR_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "KIRO_API_KEY",
)

ENGINE_CREDENTIAL_BY_ENGINE = {
    "claude_code": "ANTHROPIC_API_KEY",
    "cursor": "CURSOR_API_KEY",
    "codex": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "kiro": "KIRO_API_KEY",
    "copilot": None,  # reuses GITHUB_TOKEN
    "antigravity": None,  # OAuth-only
}

# Curated, known-good model names per LLM Judge provider -- a free-text
# model field means a typo (or a model that's been retired) only surfaces as
# a real API call failing at pipeline run time, silently skipping the review
# (see judge/factory.py::judge_configured -- the whole point of that
# fail-open behavior is nobody's PR gets blocked by it, which also means a
# typo here is easy to never notice). A dropdown makes that class of mistake
# impossible. Empty string is always a valid choice ("provider default" --
# see each provider's own DEFAULT_MODEL in judge/providers/*.py).
JUDGE_MODEL_OPTIONS_BY_PROVIDER = {
    "anthropic": ["", "claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"],
    "openai": ["", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o3", "o3-mini"],
    # Deliberately a *different* Gemini model list than what the `gemini`
    # change engine's own CLI uses internally for code changes -- the whole
    # point of the judge being a separate provider selection is a second
    # opinion, not the same model grading its own work (see judge/factory.py
    # module docstring). gemini-2.5-pro (the judge default) is a reasonable,
    # deliberately heavier choice than gemini-2.5-flash (the Jira story
    # writer's own default, see jira/story_writer.py) for exactly that
    # reason.
    "gemini": ["", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"],
}

# Same show/hide pattern as ENGINE_CREDENTIAL_BY_ENGINE, for GITHUB_TOKEN --
# it's a github-specific credential, so it shouldn't stay visible (and
# suggest it's still relevant) when a different, unimplemented provider is
# selected. None for every non-github provider since none of them have any
# credential field yet -- there's nothing to show instead.
REPO_CREDENTIAL_BY_PROVIDER = {
    "github": "GITHUB_TOKEN",
    "azure_devops": None,
    "gitlab": None,
    "bitbucket": None,
}


@dataclass
class ConfigField:
    key: str
    label: str
    group: str
    secret: bool = False
    input_type: str = "text"  # text | password | number | select | taglist | repo_list
    options: list[str] = field(default_factory=list)
    placeholder: str = ""
    help_text: str = ""


CONFIG_FIELDS: list[ConfigField] = [
    # Confluence
    ConfigField(
        "CONFLUENCE_BASE_URL", "Confluence base URL", "Confluence",
        placeholder="https://your-team.atlassian.net/wiki",
    ),
    ConfigField("CONFLUENCE_SPACE_KEY", "Space key", "Confluence", placeholder="SD"),
    ConfigField("CONFLUENCE_USER_EMAIL", "Account email", "Confluence", placeholder="you@example.com"),
    ConfigField("CONFLUENCE_API_TOKEN", "API token", "Confluence", secret=True),
    ConfigField(
        "CONFLUENCE_WEBHOOK_SECRET", "Webhook secret (optional)", "Confluence", secret=True,
        help_text="Leave blank to disable signature verification on inbound webhooks.",
    ),
    ConfigField(
        "CONFLUENCE_ALLOWED_LABELS", "Allowed page labels", "Confluence", input_type="taglist",
        help_text=(
            "Only pages carrying at least one of these Confluence labels are processed -- "
            "everything else (meeting notes, design docs, other project content) is ignored. "
            "Leave empty to process any page (no filtering). Also the label search used by polling below."
        ),
    ),
    ConfigField(
        "CONFLUENCE_POLL_ENABLED", "Poll for changes", "Confluence", input_type="select",
        options=["true", "false"],
        help_text=(
            "Confluence Cloud has no self-service webhook registration, so this is the real trigger "
            "mechanism: on a timer, searches for pages with the allowed labels above and runs the "
            "pipeline for each. The Simulate page also has a \"Poll now\" button to fire one cycle "
            "immediately, independent of this setting."
        ),
    ),
    ConfigField(
        "CONFLUENCE_POLL_INTERVAL_SECONDS", "Poll interval (seconds)", "Confluence", input_type="number",
        placeholder="300",
    ),
    # Repository
    ConfigField(
        "REPO_PROVIDER", "Repo provider", "Repository", input_type="select", options=REPO_PROVIDERS,
        help_text="Only github is currently implemented -- the others are reserved for future support.",
    ),
    ConfigField(
        "TARGET_REPOS_JSON", "Target repos", "Repository", input_type="repo_list",
        help_text=(
            "One row per repo this pipeline can touch. Test command and tech stack can both be "
            "auto-detected from what's actually in the repo (pyproject.toml, package.json, "
            "pom.xml, ...), or set by hand. Leave Routing label blank on a row to have it match "
            "every spec page (the usual single-repo setup) -- set it to route that row only to "
            "pages carrying that Confluence label, so one page can fan a coordinated change out "
            "across several repos at once. Add more than one row only for that case. Tech stack "
            "matters most for a genuinely empty repo, where the change engine has no existing code "
            "to infer conventions from -- see the field's own help text. Coding standards on a row "
            "overrides the global Coding standards fallback below for that repo only."
        ),
    ),
    ConfigField(
        "TARGET_REPO_TEST_COMMAND", "Test command (single-repo fallback)", "Repository",
        placeholder="pytest",
        help_text="Used when TARGET_REPOS_JSON is blank. Runs before the quality gate.",
    ),
    ConfigField(
        "TARGET_REPO_LINT_COMMAND", "Lint / security command (single-repo fallback)", "Repository",
        placeholder="ruff check && bandit -q -r .",
        help_text="Optional. Used after tests pass when TARGET_REPOS_JSON is blank.",
    ),
    ConfigField("GITHUB_TOKEN", "GitHub PAT", "Repository", secret=True),
    ConfigField(
        "CODING_STANDARDS", "Coding standards (global fallback)", "Repository",
        help_text=(
            "Free-text conventions (naming, layout, patterns to prefer/avoid) applied to every repo "
            "that doesn't set its own Coding standards in the Target repos table above. The change "
            "engine is also always told to look for and follow any CONTRIBUTING.md/STYLE_GUIDE.md/"
            "linter config already in the repo -- this field is for guidance that isn't written down "
            "in the repo itself."
        ),
    ),
    ConfigField(
        "STANDARDS_CONFLUENCE_PAGE_ID", "Org-wide standards page ID (optional)", "Repository",
        placeholder="123456",
        help_text=(
            "A Confluence page ID for a pinned, org-wide engineering-standards page. Fetched once "
            "per run and folded into the same prompt section as Coding standards above. Leave blank "
            "to skip."
        ),
    ),
    ConfigField(
        "PR_FEEDBACK_POLL_ENABLED", "Enable PR feedback loop", "Repository", input_type="select",
        options=["true", "false"],
        help_text=(
            "When enabled, the agent monitors open PRs carrying the agent:opened label for review "
            "comments, re-runs the change engine to address feedback, and pushes follow-up commits."
        ),
    ),
    ConfigField(
        "PR_FEEDBACK_POLL_INTERVAL_SECONDS", "PR feedback poll interval (seconds)", "Repository",
        input_type="number", placeholder="300",
    ),
    # Change engine
    ConfigField(
        "CHANGE_AGENT_ENGINE", "Change engine", "Change engine", input_type="select",
        options=ALL_ENGINES,
    ),
    ConfigField(
        "CHANGE_AGENT_MAX_TURNS", "Max turns / timeout budget", "Change engine", input_type="number",
        placeholder="30",
    ),
    ConfigField(
        "CHANGE_AGENT_MAX_ATTEMPTS", "Self-correction attempts", "Change engine", input_type="number",
        placeholder="3",
        help_text=(
            "If the repo's own tests fail, the agent gets another attempt with the failure fed back "
            "into its prompt, up to this many attempts total. 1 disables retrying."
        ),
    ),
    ConfigField(
        "ANTHROPIC_API_KEY", "Anthropic API key (claude_code)", "Change engine", secret=True,
        help_text="Also used by the LLM Judge review gate below when its provider is set to Anthropic.",
    ),
    ConfigField("CURSOR_API_KEY", "Cursor API key (cursor)", "Change engine", secret=True),
    ConfigField(
        "OPENAI_API_KEY", "OpenAI API key (codex)", "Change engine", secret=True,
        help_text="Also used by the LLM Judge review gate below when its provider is set to OpenAI.",
    ),
    ConfigField(
        "GEMINI_API_KEY", "Gemini API key (gemini)", "Change engine", secret=True,
        help_text="Also used by the LLM Judge review gate below when its provider is set to Gemini.",
    ),
    ConfigField(
        "KIRO_API_KEY", "Kiro API key (kiro)", "Change engine", secret=True,
        help_text=(
            "Requires the kiro-cli binary on PATH (curl -fsSL https://cli.kiro.dev/install | bash). "
            "This engine's exact CLI flags are sourced from Kiro's own docs, not verified against a "
            "live run -- if it fails outright on first use, check docs/change-engines.md's note on it."
        ),
    ),
    # LLM judge
    ConfigField(
        "JUDGE_ENABLED", "LLM Judge review gate", "LLM Judge", input_type="select", options=["true", "false"],
        help_text=(
            "After tests pass, an independent LLM call reviews the actual code diff against the "
            "Confluence spec change and can still reject it before a PR opens -- catches diffs that pass "
            "tests without correctly implementing the spec, or that touch unrelated files. Independent of "
            "the change engine above by design, so it isn't the same model grading its own work. If the "
            "selected provider's API key isn't set (or the call fails), this step is skipped rather than "
            "blocking the PR."
        ),
    ),
    ConfigField(
        "JUDGE_PROVIDER", "LLM Judge provider", "LLM Judge", input_type="select",
        options=["anthropic", "openai", "gemini"],
        help_text=(
            "Uses the matching API key above (Anthropic, OpenAI, or Gemini) -- not tied to "
            "CHANGE_AGENT_ENGINE, so picking Gemini here reviews with a different model than a "
            "gemini change engine used to write the code, not the same one grading its own work."
        ),
    ),
    ConfigField(
        "JUDGE_MODEL", "LLM Judge model", "LLM Judge", input_type="select",
        options=JUDGE_MODEL_OPTIONS_BY_PROVIDER["anthropic"],
        help_text="Options match the provider selected above. Blank = that provider's own default.",
    ),
    # Jira
    ConfigField(
        "JIRA_ENABLED", "Jira story tracking", "Jira", input_type="select", options=["true", "false"],
        help_text=(
            "Tracks each spec change as a Jira story -- created once a real change is confirmed, "
            "commented on (not duplicated) if the spec changes again while it's still open, and "
            "updated with the PR link or a failure explanation once the run finishes. Writes the "
            "story's description with the LLM Judge provider/model above if configured, otherwise "
            "the Gemini key from the Change Engine tab, otherwise a plain non-AI-authored summary -- "
            "no separate key needed either way."
        ),
    ),
    ConfigField("JIRA_BASE_URL", "Jira site URL", "Jira", placeholder="https://your-team.atlassian.net"),
    ConfigField("JIRA_USER_EMAIL", "Account email", "Jira", placeholder="you@example.com"),
    ConfigField("JIRA_API_TOKEN", "API token", "Jira", secret=True),
    ConfigField("JIRA_PROJECT_KEY", "Project key", "Jira", placeholder="SD"),
    ConfigField(
        "JIRA_ISSUE_TYPE", "Issue type", "Jira", input_type="select",
        options=["Story", "Task", "Bug", "Epic"],
        help_text="Must exist in the target project's issue type scheme -- team-managed Kanban projects, for example, often lack \"Story\".",
    ),
    ConfigField("JIRA_BUG_POLL_ENABLED", "Poll Jira for bugs", "Jira", input_type="select", options=["true", "false"]),
    ConfigField("JIRA_BUG_POLL_INTERVAL_SECONDS", "Bug poll interval (seconds)", "Jira", input_type="number", placeholder="300"),
    ConfigField("JIRA_BUG_JQL", "Bug polling JQL", "Jira", placeholder="project = KAN AND statusCategory != Done"),
    ConfigField("JIRA_POLL_LIMIT", "Tickets per poll", "Jira", input_type="number", placeholder="25"),
    ConfigField(
        "JIRA_SUGGEST_STORY_POINTS", "Suggest story points", "Jira", input_type="select",
        options=["true", "false"],
        help_text=(
            "Adds an \"AI-suggested complexity\" comment on the story -- never writes the real Story "
            "Points field (a per-instance custom field an LLM has no basis to fill in directly). A "
            "human still sizes the story; this is just a starting signal."
        ),
    ),
    ConfigField(
        "JIRA_APPROVAL_REQUIRED", "Require approval before implementation", "Jira", input_type="select",
        options=["true", "false"],
        help_text=(
            "Off by default -- today's behavior: the story is created and implementation starts in "
            "the same run. Turn this on to stop right after the story is created/updated and wait "
            "for it to reach the status named below before cloning/implementing anything. Also the "
            "gate \"Plan a Sprint\" approvals rely on."
        ),
    ),
    ConfigField(
        "JIRA_APPROVED_STATUS_NAME", "Approved status name", "Jira", placeholder="Approved",
        help_text=(
            "The exact Jira workflow status (not category) that means a human approved this story "
            "for implementation -- matched case-insensitively. Required for the setting above to do "
            "anything; must be a real status in this project's workflow."
        ),
    ),
    # Email
    ConfigField(
        "EMAIL_PROVIDER", "Email provider", "Email", input_type="select", options=["sendgrid", "postmark"],
        help_text="Uses the matching API key below.",
    ),
    ConfigField("SENDGRID_API_KEY", "SendGrid API key", "Email", secret=True),
    ConfigField("POSTMARK_API_KEY", "Postmark server token", "Email", secret=True),
    ConfigField("EMAIL_FROM_ADDRESS", "From address", "Email", placeholder="confluence-pr-agent@example.com"),
    ConfigField("EMAIL_TO_ADDRESSES", "To addresses", "Email", help_text="Comma-separated."),
]

CONFIG_FIELDS_BY_KEY = {f.key: f for f in CONFIG_FIELDS}
