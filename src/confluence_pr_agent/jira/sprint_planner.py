"""Plans an implementation order across a batch of BRD+sprint-tagged
Confluence pages -- see ui/plan_sprint.py's "Plan a Sprint" tab.

One batch call, not N independent per-page calls: cross-page dependency
detection genuinely needs every page's spec in view at once ("does page 2's
change assume something page 1 introduces?"), which an isolated per-page
call could never determine. Mirrors jira/story_writer.py's shape (system
prompt + structured tool schema + per-provider dispatch, preferring
JUDGE_PROVIDER/JUDGE_MODEL over a separate setting, falling back to Gemini
independently of that) for the same reasons that module gives -- this is
still "a single structured-output call to whichever LLM is already
configured," not an agentic session.

This is advisory, same as the LLM Judge: the predicted routing labels and
dependency graph are a proposal a human confirms (see ui/plan_sprint.py's
Plan -> Confirm -> Approve flow), never written to Jira or acted on directly
by this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from confluence_pr_agent.config import Settings
from confluence_pr_agent.judge.providers.anthropic_judge import DEFAULT_MODEL as ANTHROPIC_DEFAULT_MODEL
from confluence_pr_agent.judge.providers.openai_judge import DEFAULT_MODEL as OPENAI_DEFAULT_MODEL
from confluence_pr_agent.jira.story_writer import GEMINI_DEFAULT_MODEL
from confluence_pr_agent.models import PageDiff, RepoTarget

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a technical lead planning a sprint from a batch of Confluence spec pages, \
before any of them has been implemented. You are given every page's spec (or spec diff), and the \
full list of repos this platform is made of (with a short description of what each one owns).

For each page, decide:
1. predicted_labels -- which repo(s), by their routing label, this page's change genuinely needs. \
Base this only on what the spec text actually describes needing, not on what repos happen to exist. \
A change that's purely backend logic doesn't need a UI label just because a UI repo exists.
2. depends_on_page_ids -- which OTHER pages in this batch must be implemented (and merged) before \
this one can be, because this page's change assumes something the other page introduces (e.g. a \
schema field, an API endpoint, a UI component). Only declare a dependency you can point to concrete \
evidence for in the spec text -- when in doubt, leave it out; a missed dependency is a human's call \
to catch during review, a false one wastes an implementation slot waiting on nothing.
3. dependency_rationale -- one sentence per page citing exactly what the dependency is, empty if \
depends_on_page_ids is empty.

You are proposing a plan for a human to review and approve, not making an implementation decision \
yourself -- prefer flagging genuine uncertainty over guessing confidently."""

PLAN_TOOL_NAME = "submit_sprint_plan"
PLAN_TOOL_DESCRIPTION = "Submit the predicted routing labels and dependency graph for this sprint's pages."

PLAN_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "predicted_labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Routing label(s) this page's change genuinely needs, e.g. [\"repo-api\"].",
                    },
                    "depends_on_page_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "page_ids from this same batch that must land first. Empty if none.",
                    },
                    "dependency_rationale": {
                        "type": "string",
                        "description": "One sentence citing the concrete dependency. Empty string if none.",
                    },
                },
                "required": ["page_id", "predicted_labels", "depends_on_page_ids", "dependency_rationale"],
            },
        },
    },
    "required": ["pages"],
}


@dataclass
class PagePlan:
    page_id: str
    predicted_labels: list[str] = field(default_factory=list)
    depends_on_page_ids: list[str] = field(default_factory=list)
    dependency_rationale: str = ""


@dataclass
class SprintPlanContent:
    pages: list[PagePlan] = field(default_factory=list)


def build_prompt(pages: list[PageDiff], repo_targets: list[RepoTarget]) -> str:
    lines = ["This platform's repos:\n"]
    for rt in repo_targets:
        label = rt.label or "(no routing label configured -- matches every page)"
        stack = f" -- {rt.tech_stack}" if rt.tech_stack else ""
        lines.append(f"- `{rt.label or rt.target_repo}` ({rt.target_repo}{stack}): routing label `{label}`")

    lines.append("\nPages in this sprint batch:\n")
    for diff in pages:
        lines.append(f"--- page_id: {diff.page.page_id} -- \"{diff.page.title}\" ---")
        lines.append(diff.diff_text)
        lines.append("")

    return "\n".join(lines)


def _content_from_tool_input(data: dict) -> SprintPlanContent:
    return SprintPlanContent(
        pages=[
            PagePlan(
                page_id=p["page_id"],
                predicted_labels=list(p.get("predicted_labels") or []),
                depends_on_page_ids=list(p.get("depends_on_page_ids") or []),
                dependency_rationale=p.get("dependency_rationale") or "",
            )
            for p in data.get("pages", [])
        ]
    )


def _fallback_content(pages: list[PageDiff]) -> SprintPlanContent:
    """No provider configured, or the call failed -- every page still gets
    a PagePlan (empty predictions, no dependencies), same "always produce
    something, degrade the content not the existence" idiom as
    story_writer.py's _fallback_content. A human reviewing an empty plan in
    /ui/plan-sprint sees no predictions rather than the whole feature
    silently doing nothing.
    """
    return SprintPlanContent(pages=[PagePlan(page_id=diff.page.page_id) for diff in pages])


async def _generate_anthropic(settings: Settings, pages: list[PageDiff], repo_targets: list[RepoTarget]) -> SprintPlanContent:
    from anthropic import AsyncAnthropic

    async with AsyncAnthropic(api_key=settings.anthropic_api_key) as client:
        response = await client.messages.create(
            model=settings.judge_model or ANTHROPIC_DEFAULT_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(pages, repo_targets)}],
            tools=[{"name": PLAN_TOOL_NAME, "description": PLAN_TOOL_DESCRIPTION, "input_schema": PLAN_TOOL_PARAMETERS}],
            tool_choice={"type": "tool", "name": PLAN_TOOL_NAME},
        )

    for block in response.content:
        if block.type == "tool_use" and block.name == PLAN_TOOL_NAME:
            return _content_from_tool_input(block.input)
    raise RuntimeError("sprint planner (anthropic) did not return a structured response")


async def _generate_openai(settings: Settings, pages: list[PageDiff], repo_targets: list[RepoTarget]) -> SprintPlanContent:
    import json

    from openai import AsyncOpenAI

    async with AsyncOpenAI(api_key=settings.openai_api_key) as client:
        response = await client.chat.completions.create(
            model=settings.judge_model or OPENAI_DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(pages, repo_targets)},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {"name": PLAN_TOOL_NAME, "description": PLAN_TOOL_DESCRIPTION, "parameters": PLAN_TOOL_PARAMETERS},
                }
            ],
            tool_choice={"type": "function", "function": {"name": PLAN_TOOL_NAME}},
        )

    for call in response.choices[0].message.tool_calls or []:
        if call.function.name == PLAN_TOOL_NAME:
            return _content_from_tool_input(json.loads(call.function.arguments))
    raise RuntimeError("sprint planner (openai) did not return a structured response")


async def _generate_gemini(settings: Settings, pages: list[PageDiff], repo_targets: list[RepoTarget]) -> SprintPlanContent:
    import json

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    response = await client.aio.models.generate_content(
        model=settings.judge_model or GEMINI_DEFAULT_MODEL,
        contents=build_prompt(pages, repo_targets),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_json_schema=PLAN_TOOL_PARAMETERS,
        ),
    )
    if not response.text:
        raise RuntimeError("sprint planner (gemini) did not return a response body")
    return _content_from_tool_input(json.loads(response.text))


async def generate_sprint_plan(
    settings: Settings, pages: list[PageDiff], repo_targets: list[RepoTarget]
) -> SprintPlanContent:
    """Same fail-open provider cascade as story_writer.py::generate_story_content
    -- JUDGE_PROVIDER's own key first, then GEMINI_API_KEY independently of
    that, then a plain no-prediction fallback so the sprint plan still
    exists (just empty) rather than the whole "Plan a Sprint" action erroring
    out.
    """
    provider = settings.judge_provider.strip().lower()
    try:
        if provider == "anthropic" and settings.anthropic_api_key:
            return await _generate_anthropic(settings, pages, repo_targets)
        if provider == "openai" and settings.openai_api_key:
            return await _generate_openai(settings, pages, repo_targets)
    except Exception as exc:
        logger.warning("Sprint plan generation failed via judge provider %r; falling back: %s", provider, exc)

    if settings.gemini_api_key:
        try:
            return await _generate_gemini(settings, pages, repo_targets)
        except Exception as exc:
            logger.warning("Sprint plan generation failed via Gemini; using an empty fallback: %s", exc)

    return _fallback_content(pages)
