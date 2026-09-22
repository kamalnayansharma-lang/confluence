from __future__ import annotations

from confluence_pr_agent.jira import story_writer
from confluence_pr_agent.models import PageDiff, PageSnapshot


import pytest

_STORY = story_writer.JiraStoryContent(
    summary="Support PayPal at checkout",
    description="The spec now requires PayPal as a payment option.",
    acceptance_criteria=["Customer can select PayPal"],
    complexity="M",
    complexity_reason="Touches the payment integration.",
)


def _diff() -> PageDiff:
    page = PageSnapshot(
        page_id="123456", title="Checkout Flow Spec", version=2, body_html="<p>spec v2</p>",
        url="https://example.atlassian.net/wiki/spaces/SD/pages/123456",
    )
    return PageDiff(
        page=page, previous_version=1, diff_text="-old line\n+new line", is_first_seen=False,
        body_checksum="abc123",
    )


def test_fallback_content_is_detailed_and_has_no_raw_diff_text():
    """The fallback description remains readable without an LLM/Rovo call."""
    content = story_writer._fallback_content(_diff())
    assert "-old line" not in content.description
    assert "+new line" not in content.description
    assert "Checkout Flow Spec" in content.description
    assert "Problem and context" in content.description
    assert "Implementation guidance" in content.description
    assert "Validation" in content.description
    assert len(content.acceptance_criteria) == 3


async def test_repo_file_plan_adds_repo_and_file_change_guidance(settings, monkeypatch):
    class FakeGitHubClient:
        def __init__(self, token):
            pass

        async def get_repo_file_tree(self, owner_repo, branch):
            return ["src/booking_service.py", "tests/test_booking_service.py", "README.md"]

        async def aclose(self):
            pass

    monkeypatch.setattr(story_writer, "GitHubClient", FakeGitHubClient)
    settings.github_token = "github-test"
    story = await story_writer._append_repo_file_plan(settings, _diff(), _STORY)

    assert "### Repository/File Implementation Plan" in story.description
    assert "#### acme/widgets" in story.description
    assert "tests/test_booking_service.py" in story.description
    assert "Add or update regression tests" in story.description


async def test_prefers_anthropic_when_judge_provider_is_anthropic_and_key_set(settings, monkeypatch):
    settings.judge_provider = "anthropic"
    settings.anthropic_api_key = "sk-test"
    settings.gemini_api_key = "gemini-test-key"  # present but must not be tried

    async def _fake_anthropic(settings, diff):
        return _STORY

    async def _boom_gemini(settings, diff):
        raise AssertionError("gemini should not be tried when anthropic succeeds")

    monkeypatch.setattr(story_writer, "_generate_anthropic", _fake_anthropic)
    monkeypatch.setattr(story_writer, "_generate_gemini", _boom_gemini)

    result = await story_writer.generate_story_content(settings, _diff())
    assert result is _STORY


async def test_falls_back_to_gemini_when_judge_provider_key_is_missing(settings, monkeypatch):
    settings.judge_provider = "anthropic"
    settings.anthropic_api_key = ""  # not set -- anthropic branch never attempted
    settings.gemini_api_key = "gemini-test-key"

    async def _fake_gemini(settings, diff):
        return _STORY

    monkeypatch.setattr(story_writer, "_generate_gemini", _fake_gemini)

    result = await story_writer.generate_story_content(settings, _diff())
    assert result is _STORY


async def test_falls_back_to_gemini_when_judge_provider_call_fails(settings, monkeypatch):
    settings.judge_provider = "anthropic"
    settings.anthropic_api_key = "sk-test"
    settings.gemini_api_key = "gemini-test-key"

    async def _fail_anthropic(settings, diff):
        raise RuntimeError("anthropic is down")

    async def _fake_gemini(settings, diff):
        return _STORY

    monkeypatch.setattr(story_writer, "_generate_anthropic", _fail_anthropic)
    monkeypatch.setattr(story_writer, "_generate_gemini", _fake_gemini)

    result = await story_writer.generate_story_content(settings, _diff())
    assert result is _STORY


async def test_falls_back_to_detailed_content_when_nothing_is_configured(settings):
    settings.judge_provider = "anthropic"
    settings.anthropic_api_key = ""
    settings.openai_api_key = ""
    settings.gemini_api_key = ""

    result = await story_writer.generate_story_content(settings, _diff())
    assert "Implementation guidance" in result.description
    assert result.acceptance_criteria


async def test_falls_back_to_detailed_content_when_gemini_also_fails(settings, monkeypatch):
    settings.judge_provider = "anthropic"
    settings.anthropic_api_key = ""
    settings.gemini_api_key = "gemini-test-key"

    async def _fail_gemini(settings, diff):
        raise RuntimeError("gemini is down")

    monkeypatch.setattr(story_writer, "_generate_gemini", _fail_gemini)

    result = await story_writer.generate_story_content(settings, _diff())
    assert "Problem and context" in result.description
    assert "AI-generated description unavailable" not in result.description
