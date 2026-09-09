"""Writes the Jira story's title/description/acceptance-criteria (and,
optionally, a complexity suggestion) from the Confluence spec diff -- before
any code exists, so this only ever sees the spec, not the eventual change.

Prefers JUDGE_PROVIDER/JUDGE_MODEL (config.py) over a separate provider
setting -- it's the same kind of thing, a single structured-output call to
whichever LLM is already configured, not an agentic session. But the LLM
Judge is optional and often left unconfigured (or configured with a key
that turns out not to work), which would otherwise silently leave every
story on the plain-text fallback -- so if GEMINI_API_KEY is set, it's tried
next, independent of JUDGE_PROVIDER: CHANGE_AGENT_ENGINE=gemini already
requires that key to be live for the pipeline to do anything at all, making
it the one path here that doesn't depend on the judge setup being right too.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from confluence_pr_agent.config import Settings
from confluence_pr_agent.judge.providers.anthropic_judge import DEFAULT_MODEL as ANTHROPIC_DEFAULT_MODEL
from confluence_pr_agent.judge.providers.openai_judge import DEFAULT_MODEL as OPENAI_DEFAULT_MODEL
from confluence_pr_agent.models import PageDiff
from confluence_pr_agent.repo.github_client import GitHubClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a product-savvy engineer writing a detailed Jira story from a \
Confluence spec change, before any code has been written. The description must be useful to a \
developer who has not seen the Confluence page. Produce:

1. summary -- a short, specific issue title (not just the Confluence page title verbatim).
2. description -- a detailed, structured description using these headings: Problem and context, \
Requested behavior, Implementation plan, Validation, and Out of scope. Explain what changed, why \
it matters, the likely application/data/API surfaces to inspect, and how the change should be tested. \
Use concrete details from the spec; do not invent file names or repositories that are not provided.
3. acceptance_criteria -- 3-6 concrete, testable bullet points a reviewer could check off.
4. complexity -- a rough T-shirt-size estimate (S/M/L/XL) of how much work this looks like, plus \
one sentence of reasoning. This is a starting signal for a human, not a committed estimate -- you \
have no visibility into this team's velocity or codebase conventions, so keep the reasoning \
grounded in what the spec diff itself shows (the scope of the described behavior), not invented \
context."""

STORY_TOOL_NAME = "submit_story"
STORY_TOOL_DESCRIPTION = "Submit the Jira story content for this spec change."
STORY_TOOL_PARAMETERS: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "A short, single-line Jira issue title."},
        "description": {
            "type": "string",
            "description": "Detailed structured description with implementation plan and validation.",
        },
        "acceptance_criteria": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-6 concrete, testable acceptance criteria.",
        },
        "complexity": {"type": "string", "enum": ["S", "M", "L", "XL"]},
        "complexity_reason": {"type": "string", "description": "One sentence justifying the estimate."},
    },
    "required": ["summary", "description", "acceptance_criteria", "complexity", "complexity_reason"],
}


@dataclass
class JiraStoryContent:
    summary: str
    description: str
    acceptance_criteria: list[str] = field(default_factory=list)
    complexity: str | None = None
    complexity_reason: str | None = None


def build_prompt(diff: PageDiff) -> str:
    header = f"Confluence spec page: {diff.page.title}\nPage URL: {diff.page.url}\n\n"
    if diff.related_context:
        header += (
            "Related prior work from other Confluence pages, for context only -- do not treat this "
            "as part of the current spec change, it's background:\n\n" + diff.related_context + "\n\n"
        )
    if diff.is_first_seen:
        body = (
            "This is the first time this page has been processed. Below is the full current "
            "spec.\n\n" + diff.diff_text
        )
    else:
        body = (
            f"The spec changed (v{diff.previous_version} -> v{diff.page.version}), shown below as "
            "a unified diff of the page's plain-text content.\n\n" + diff.diff_text
        )
    return header + body


def _content_from_tool_input(data: dict) -> JiraStoryContent:
    return JiraStoryContent(
        summary=data["summary"],
        description=data["description"],
        acceptance_criteria=list(data.get("acceptance_criteria") or []),
        complexity=data.get("complexity"),
        complexity_reason=data.get("complexity_reason"),
    )


def _fallback_content(diff: PageDiff) -> JiraStoryContent:
    """Build a detailed, deterministic ticket when an LLM/Rovo-style call is unavailable.

    Jira's public REST API can create and update issue content but does not
    expose a supported endpoint for invoking Rovo. This fallback keeps every
    generated issue useful instead of leaving a one-line placeholder.
    """
    spec_text = _fallback_spec_text(diff)
    description = (
        f"Problem and context\n\n"
        f"This ticket captures the requested change from the Confluence page "
        f"[{diff.page.title}]({diff.page.url}).\n\n"
        f" Requested behavior\n\n{spec_text}\n\n"
        "Implementation guidance\n\n"
        "Inspect the affected application flow, its data/API boundaries, and the existing tests "
        "before changing code. Preserve existing behavior outside this request, add a regression "
        "test for the reported case, and update related documentation or contracts if they change.\n\n"
        "## Validation\n\n"
        "Run the repository's configured test command and verify both the reported scenario and "
        "the existing behavior that must remain unchanged.\n\n"
        "## Source\n\n"
        f"Confluence page: {diff.page.url}"
    )
    return JiraStoryContent(
        summary=f"Sync with Confluence: {diff.page.title}",
        description=description,
        acceptance_criteria=[
            "The requested behavior is implemented for the scenario described in the Confluence change.",
            "Existing behavior outside the requested change remains unchanged.",
            "A regression test covers the reported scenario and passes with the configured test command.",
        ],
        complexity="M",
        complexity_reason="Medium default because the affected code paths are not verified until the agent inspects the repository.",
    )


def _fallback_spec_text(diff: PageDiff) -> str:
    """Extract readable new/current spec text without dumping a unified diff."""
    if diff.is_first_seen:
        return diff.diff_text[:8000]
    lines = []
    for line in diff.diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
    return "\n".join(lines)[:8000] or "The Confluence page was updated; inspect the linked page and the diff comment for the exact change."


async def _append_repo_file_plan(settings: Settings, diff: PageDiff, story: JiraStoryContent) -> JiraStoryContent:
    """Append repo/file-level guidance grounded in each configured Git tree."""
    if not settings.github_token:
        return story
    keywords = {
        word.lower()
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_/-]{2,}", f"{story.summary} {diff.diff_text}")
    }
    github = GitHubClient(settings.github_token)
    by_repo: dict[str, list[str]] = {}
    try:
        for target in settings.resolved_repo_targets:
            try:
                tree = await github.get_repo_file_tree(target.target_repo, target.base_branch)
            except Exception as exc:
                logger.warning("Could not inspect file tree for Jira story plan in %s: %s", target.target_repo, exc)
                continue
            candidates = [
                path for path in tree
                if any(keyword in path.lower() for keyword in keywords)
                or "/test" in path.lower()
                or path.lower().startswith("test")
            ][:10]
            by_repo[target.target_repo] = candidates or tree[:10]
    finally:
        await github.aclose()
    if not by_repo:
        return story

    lines = [
        "### Repository/File Implementation Plan",
        "These are repository-grounded candidate files. The coding agent must verify the exact call path before editing.",
    ]
    for repo, paths in by_repo.items():
        lines.append(f"#### {repo}")
        for path in paths:
            if "/test" in path.lower() or path.lower().startswith("test"):
                change = "Add or update regression tests for the requested behavior."
            else:
                change = f"Inspect and update the implementation related to {story.summary.lower()}."
            lines.append(f"- `{path}` -- {change}")
    story.description = story.description.rstrip() + "\n\n" + "\n".join(lines)
    return story


async def _generate_anthropic(settings: Settings, diff: PageDiff) -> JiraStoryContent:
    from anthropic import AsyncAnthropic

    async with AsyncAnthropic(api_key=settings.anthropic_api_key) as client:
        response = await client.messages.create(
            model=settings.judge_model or ANTHROPIC_DEFAULT_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(diff)}],
            tools=[{"name": STORY_TOOL_NAME, "description": STORY_TOOL_DESCRIPTION, "input_schema": STORY_TOOL_PARAMETERS}],
            tool_choice={"type": "tool", "name": STORY_TOOL_NAME},
        )

    for block in response.content:
        if block.type == "tool_use" and block.name == STORY_TOOL_NAME:
            return _content_from_tool_input(block.input)
    raise RuntimeError("story writer (anthropic) did not return a structured response")


async def _generate_openai(settings: Settings, diff: PageDiff) -> JiraStoryContent:
    import json

    from openai import AsyncOpenAI

    async with AsyncOpenAI(api_key=settings.openai_api_key) as client:
        response = await client.chat.completions.create(
            model=settings.judge_model or OPENAI_DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(diff)},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": STORY_TOOL_NAME,
                        "description": STORY_TOOL_DESCRIPTION,
                        "parameters": STORY_TOOL_PARAMETERS,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": STORY_TOOL_NAME}},
        )

    for call in response.choices[0].message.tool_calls or []:
        if call.function.name == STORY_TOOL_NAME:
            return _content_from_tool_input(json.loads(call.function.arguments))
    raise RuntimeError("story writer (openai) did not return a structured response")


GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"


async def _generate_gemini(settings: Settings, diff: PageDiff) -> JiraStoryContent:
    import json

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    response = await client.aio.models.generate_content(
        model=settings.judge_model or GEMINI_DEFAULT_MODEL,
        contents=build_prompt(diff),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            # response_json_schema (not the stricter response_schema, which
            # wants a genai.types.Schema object) accepts the same plain
            # JSON-schema dict already used for the Anthropic/OpenAI tool
            # definitions above -- one schema, three providers.
            response_mime_type="application/json",
            response_json_schema=STORY_TOOL_PARAMETERS,
        ),
    )
    if not response.text:
        raise RuntimeError("story writer (gemini) did not return a response body")
    return _content_from_tool_input(json.loads(response.text))


async def generate_story_content(settings: Settings, diff: PageDiff) -> JiraStoryContent:
    """Fails open to a plain, non-AI-authored fallback -- a story must still
    get created either way (see pipeline/orchestrator.py), so a misconfigured
    or unreachable provider degrades the story's content, not whether one
    exists at all.

    Tries JUDGE_PROVIDER's own key first, then GEMINI_API_KEY independently
    of what JUDGE_PROVIDER is set to (see module docstring for why), before
    giving up on AI-authored content entirely.
    """
    provider = settings.judge_provider.strip().lower()
    story: JiraStoryContent | None = None
    try:
        if provider == "anthropic" and settings.anthropic_api_key:
            story = await _generate_anthropic(settings, diff)
        if provider == "openai" and settings.openai_api_key:
            story = await _generate_openai(settings, diff)
    except Exception as exc:
        logger.warning(
            "Jira story content generation failed for page %s via judge provider %r; "
            "falling back: %s",
            diff.page.page_id, provider, exc,
        )

    if settings.gemini_api_key:
        try:
            story = await _generate_gemini(settings, diff)
        except Exception as exc:
            logger.warning(
                "Jira story content generation failed for page %s via Gemini; using a plain "
                "fallback: %s",
                diff.page.page_id, exc,
            )

    story = story or _fallback_content(diff)
    return await _append_repo_file_plan(settings, diff, story)
