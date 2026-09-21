"""Shared data types passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PageSnapshot:
    """A single fetched revision of a Confluence page."""

    page_id: str
    title: str
    version: int
    body_html: str
    url: str
    labels: list[str] = field(default_factory=list)


@dataclass
class PageDiff:
    """What changed between the previously stored snapshot and the current one."""

    page: PageSnapshot
    previous_version: int | None
    diff_text: str
    is_first_seen: bool
    body_checksum: str  # sha256 of page's normalized plain-text body -- see confluence/diff.py
    content_unchanged: bool = False  # version bumped (e.g. metadata edit) but checksum matched
    # True when a human hit "Retrigger" on a run whose page had no textual
    # change since -- see confluence/diff.py::compute_diff's `force` param
    # and ui/routes.py's retrigger route. The usual case: labels were added
    # to route a previously-flagged repo gap in, but nobody edited the page
    # body (a label-only edit doesn't change body_checksum, so without this
    # the run would just re-hit content_unchanged and skip again).
    forced: bool = False
    # Set by pipeline/orchestrator.py only for a genuinely multi-repo run --
    # a short description of which repos are checked out as which
    # subdirectories of the agent's working directory, so a coupled change
    # can be made coherently across them. None for the (default, unchanged)
    # single-repo case. See agent/prompts.py::build_repo_context/
    # build_user_prompt -- kept on PageDiff rather than a new parameter on
    # ChangeEngine.implement_change so every engine implementation (already
    # taking `diff` unchanged) needs no signature change at all.
    repo_context: str | None = None
    # Plain-text content of the pinned org-wide standards page
    # (Settings.standards_confluence_page_id), fetched once per run in
    # pipeline/orchestrator.py the same way repo_context is precomputed
    # above -- kept here rather than re-fetched inside prompt-building so
    # every ChangeEngine implementation still takes `diff` unchanged.
    standards_text: str | None = None
    # Bounded "related prior work" context assembled by
    # jira/story_writer.py::_gather_related_context from other Confluence
    # pages (explicit in-body links first, a title keyword search as
    # fallback) -- title + short excerpt per related page, not full bodies.
    related_context: str | None = None


@dataclass
class ChangeAgentResult:
    """Output of the code-change agent run against the target repo."""

    success: bool
    summary: str
    files_changed: list[str] = field(default_factory=list)
    raw_log: str = ""
    # Whatever token/cost usage the engine's own output reported, passed
    # through as-is rather than normalized -- each CLI reports this
    # differently (or not at all) and the shapes aren't worth force-fitting
    # into one schema. None means the engine didn't report anything parseable.
    usage: dict | None = None


@dataclass
class RepoTestResult:
    passed: bool
    output: str
    command: str
    # True when `command` itself couldn't even be run -- malformed shell
    # quoting, or its binary isn't on PATH -- as distinct from `passed=False`
    # meaning the command ran and genuinely failed. See
    # testing/test_runner.py::run_tests for where this gets set, and
    # pipeline/orchestrator.py for why it matters: an unusable test_command
    # still correctly fails the run either way, but an unusable
    # lint_command (optional) fails OPEN when this is True, instead of
    # blocking a PR over what's actually a config typo, not a real lint
    # violation.
    crashed: bool = False


@dataclass
class JudgeCriterion:
    """One independently-scored dimension of the judge's review -- see
    judge/prompts.py::CRITERIA for the fixed set of three. Rendered as a
    row in the rubric table attached to the PR body.
    """

    key: str  # e.g. "implements_spec" -- matches judge/prompts.py::CRITERIA
    label: str  # human-readable, e.g. "Implements the spec change"
    assessment: str  # "pass" | "warning" | "fail"
    note: str  # one sentence, specific to this diff


@dataclass
class JudgeResult:
    """Verdict from the LLM-as-judge review (pipeline/orchestrator.py),
    which runs after tests pass but before a PR is opened -- a green test
    suite doesn't prove the spec was actually implemented (or implemented
    without unrelated changes), so this is a second, semantic review.

    `verdict` is derived from `criteria` (judge/prompts.py::derive_verdict),
    not reported by the model directly -- any "fail" criterion makes the
    whole thing "rejected", any "warning" with no "fail" makes it
    "approved_with_warnings", otherwise "approved". Unlike the tests gate,
    the judge never blocks a PR from opening -- see orchestrator.py for how
    "rejected" changes what kind of PR opens instead of stopping the run.
    """

    verdict: str  # "approved" | "approved_with_warnings" | "rejected" | "skipped" (disabled, or the call failed)
    reasoning: str
    concerns: list[str] = field(default_factory=list)
    criteria: list[JudgeCriterion] = field(default_factory=list)


@dataclass
class RepoTarget:
    """One repo a spec change can be routed to. Settings.resolved_repo_targets
    (config.py) returns a list of these -- either parsed from
    TARGET_REPOS_JSON (real multi-repo config) or a single synthetic entry
    built from the legacy TARGET_REPO/TARGET_REPO_BASE_BRANCH/
    TARGET_REPO_TEST_COMMAND fields, with an empty label. Empty label means
    "matches every page" -- same semantics CONFLUENCE_ALLOWED_LABELS already
    uses for "no filter configured" -- so a page is in scope for this repo
    whenever the label is blank or the page actually carries it.
    """

    target_repo: str  # owner/name
    base_branch: str
    test_command: str
    label: str = ""
    # Free-text stack description (e.g. "Python 3.12 + FastAPI"), surfaced to
    # the change engine so a genuinely empty/near-empty repo has something to
    # scaffold against instead of guessing -- see agent/prompts.py. Detected
    # (not required) from the repo's root files by
    # repo/test_command_detection.py::detect_tech_stack, same "prefill once
    # in the UI, don't re-guess per run" idiom as test_command already uses.
    tech_stack: str = ""
    # Free-text conventions specific to this repo (naming, layout, patterns
    # to prefer/avoid) -- folds into the same prompt section as tech_stack.
    # Settings.coding_standards (config.py) is a global fallback used when
    # this is blank.
    coding_standards: str = ""
    # Optional. Run after test_command passes, same pass/fail gate, same
    # self-correction retry loop -- see pipeline/orchestrator.py. Blank
    # (default) means no lint step at all for this repo.
    lint_command: str = ""


@dataclass
class RepoChangeResult:
    """One repo's outcome within a pipeline run. A run with N in-scope repos
    (see pipeline/orchestrator.py) produces N of these -- PipelineResult.
    repo_results / RunRecord.repo_results below.
    """

    target_repo: str
    # "opened_pr" | "no_changes" (agent didn't touch this repo) |
    # "tests_failed" | "judge_rejected" | "error"
    status: str
    files_changed: list[str] = field(default_factory=list)
    pull_request: "PullRequestResult | None" = None
    reused_pr: bool = False
    judge: "JudgeResult | None" = None
    tests: "RepoTestResult | None" = None
    error: str | None = None


@dataclass
class PullRequestResult:
    number: int
    url: str
    branch: str
    draft: bool = False


@dataclass
class PullRequestStatus:
    """Current state of a previously-opened PR, fetched fresh each run to
    decide whether to reuse its branch -- see pipeline/orchestrator.py.
    """

    number: int
    state: str  # "open" | "closed"
    merged: bool
    labels: list[str] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.state == "open"


@dataclass
class JiraIssueResult:
    key: str  # e.g. "SD-123"
    url: str


@dataclass
class JiraIssueStatus:
    """Current state of a previously-created Jira issue, fetched fresh each
    run to decide whether to reuse it -- mirrors PullRequestStatus's role
    for PR-branch reuse. Jira's statusCategory (not the workflow-specific
    status name, which varies per project) is the reliable "is this done"
    signal across arbitrary team workflows.
    """

    key: str
    status_name: str
    status_category: str  # "new" | "indeterminate" | "done"

    @property
    def is_open(self) -> bool:
        return self.status_category != "done"


@dataclass
class PipelineResult:
    # "opened_pr" | "no_change_detected" | "tests_failed" | "judge_rejected" | "error" | "ignored"
    # judge_rejected still carries a `pull_request` -- the judge never blocks
    # PR creation, it only changes the PR into a flagged draft. Only a
    # tests_failed/error/ignored/no_change_detected run has no PR.
    status: str
    page: PageSnapshot | None = None
    diff: PageDiff | None = None
    change: ChangeAgentResult | None = None
    tests: RepoTestResult | None = None
    judge: JudgeResult | None = None
    pull_request: PullRequestResult | None = None
    # True when `pull_request` is an existing still-open PR that was updated
    # in place (new commits pushed, title/body/labels refreshed) rather than
    # a brand-new one -- see pipeline/orchestrator.py's branch-reuse logic.
    reused_pr: bool = False
    jira_issue: JiraIssueResult | None = None
    jira_reused: bool = False  # true if an existing still-open story was commented on rather than a new one created
    error: str | None = None
    # Email is best-effort and independent of pipeline success: a PR that
    # opened successfully stays status="opened_pr" even if the notification
    # email fails afterward (e.g. SendGrid not configured) -- that failure
    # is recorded here instead of overwriting the whole run as an error.
    email_sent: bool = False
    email_error: str | None = None
    attempts: int = 1  # how many ai_agent/run_tests cycles this run took -- see change_agent_max_attempts
    # Per-repo detail for a (possibly multi-repo) run -- see RepoTarget's
    # docstring. `pull_request`/`reused_pr` above stay populated from the
    # first repo in this list that has one, for the single-repo UI
    # (runs.html/run_detail.html), which doesn't render this list yet --
    # see the plan's UI follow-up. Always exactly one entry for a
    # single-repo (today's default/fallback) run.
    repo_results: list[RepoChangeResult] = field(default_factory=list)


@dataclass
class RunRecord:
    """One row in the run-history store (data/runs.json) -- what the /ui/runs
    dashboard lists, and /ui/runs/{run_id} shows in full. Written once per
    pipeline invocation, regardless of outcome, so failed/skipped runs stay
    visible too.
    """

    run_id: str
    started_at: str  # ISO 8601
    finished_at: str  # ISO 8601
    duration_seconds: float
    page_id: str
    page_title: str
    confluence_url: str
    engine: str
    target_repo: str
    status: str
    current_stage: str | None = None  # last stage entered -- see pipeline/stages.py
    files_changed: list[str] = field(default_factory=list)
    pr_number: int | None = None
    pr_url: str | None = None
    pr_draft: bool = False  # true when the judge rejected but a flagged PR still opened -- see orchestrator.py
    reused_pr: bool = False  # true if this run updated an existing still-open PR instead of opening a new one
    summary: str | None = None
    error: str | None = None
    email_sent: bool = False
    email_error: str | None = None
    usage: dict | None = None
    raw_log: str | None = None  # tail of the engine's own output; detail-page only
    test_output: str | None = None  # tail of the target repo's test command output; detail-page only
    judge_verdict: str | None = None  # "approved" | "approved_with_warnings" | "rejected" | "skipped"
    judge_reasoning: str | None = None
    judge_concerns: list[str] = field(default_factory=list)
    judge_criteria: list[JudgeCriterion] = field(default_factory=list)
    spec_diff: str | None = None  # the Confluence diff text the agent was actually prompted with
    attempts: int = 1  # how many ai_agent/run_tests cycles this run took -- see agent_max_attempts
    max_attempts: int = 1  # the CHANGE_AGENT_MAX_ATTEMPTS ceiling in effect for this run
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    jira_reused: bool = False  # true if an existing still-open story was commented on rather than a new one created
    # Per-repo detail -- see PipelineResult.repo_results above for why
    # target_repo/pr_number/pr_url/pr_draft/reused_pr above stay populated
    # too (first repo with a PR, for today's single-repo UI). Old run
    # records predating this field simply won't have the key -- Jinja's
    # Undefined handles that as empty, not an error.
    repo_results: list[RepoChangeResult] = field(default_factory=list)
    # True when the change agent's own summary flagged that this change
    # likely needs a repo outside what was checked out for this run -- see
    # agent/prompts.py::build_repo_context's out-of-scope section, which
    # instructs the agent to end its summary with a "Next steps" section
    # naming the repo(s)/label(s) still needed. Detected structurally off
    # that exact heading (set once in orchestrator.py::finish), not
    # re-derived from free text in the template every render -- deliberately
    # NOT set just because this user has other configured repos out of
    # scope (that's the normal, expected case for most multi-repo changes);
    # it only fires when the agent itself judged a real gap exists.
    flagged_scope_gap: bool = False
