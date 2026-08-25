from __future__ import annotations

import pytest

from confluence_pr_agent.agent.engines.antigravity_cli import AntigravityCliEngine
from confluence_pr_agent.agent.engines.antigravity_cli import parse_response as parse_antigravity_response
from confluence_pr_agent.agent.engines.antigravity_cli import parse_usage as parse_antigravity_usage
from confluence_pr_agent.agent.engines.claude_code import ClaudeCodeEngine
from confluence_pr_agent.agent.engines.codex_cli import CodexCliEngine, parse_usage as parse_codex_usage
from confluence_pr_agent.agent.engines.copilot_cli import CopilotCliEngine
from confluence_pr_agent.agent.engines.cursor_cli import CursorCliEngine, parse_result as parse_cursor_result
from confluence_pr_agent.agent.engines.cursor_cli import parse_usage as parse_cursor_usage
from confluence_pr_agent.agent.engines.gemini_cli import GeminiCliEngine, parse_response as parse_gemini_response
from confluence_pr_agent.agent.engines.gemini_cli import parse_usage as parse_gemini_usage
from confluence_pr_agent.agent.engines.kiro_cli import KiroCliEngine
from confluence_pr_agent.agent.factory import build_change_engine


@pytest.mark.parametrize(
    "name,expected_type",
    [
        ("claude_code", ClaudeCodeEngine),
        ("Claude_Code", ClaudeCodeEngine),  # case-insensitive
        ("cursor", CursorCliEngine),
        ("copilot", CopilotCliEngine),
        ("codex", CodexCliEngine),
        ("gemini", GeminiCliEngine),
        ("antigravity", AntigravityCliEngine),
        ("kiro", KiroCliEngine),
    ],
)
def test_build_change_engine_dispatches_by_name(name, expected_type, settings):
    settings.change_agent_engine = name
    assert isinstance(build_change_engine(settings), expected_type)


def test_build_change_engine_rejects_unknown_name(settings):
    settings.change_agent_engine = "some-other-tool"
    with pytest.raises(ValueError, match="Unknown CHANGE_AGENT_ENGINE"):
        build_change_engine(settings)


def test_build_change_engine_passes_openai_key_to_codex(settings):
    settings.change_agent_engine = "codex"
    settings.openai_api_key = "sk-test-123"
    engine = build_change_engine(settings)
    assert engine._api_key == "sk-test-123"


def test_build_change_engine_passes_gemini_key_to_gemini(settings):
    settings.change_agent_engine = "gemini"
    settings.gemini_api_key = "gm-test-123"
    engine = build_change_engine(settings)
    assert engine._api_key == "gm-test-123"


def test_build_change_engine_passes_kiro_key_to_kiro(settings):
    settings.change_agent_engine = "kiro"
    settings.kiro_api_key = "kiro-test-123"
    engine = build_change_engine(settings)
    assert engine._api_key == "kiro-test-123"


async def test_kiro_engine_reports_missing_binary_clearly(settings, tmp_path, monkeypatch):
    """Doesn't need a real kiro-cli install -- confirms the "not found on
    PATH" fallback (shared shape with every other CLI-based engine) fires
    correctly rather than raising, since this is the one engine whose exact
    flags haven't been verified against a live binary (see
    docs/change-engines.md).
    """
    from confluence_pr_agent.models import PageDiff, PageSnapshot

    monkeypatch.setattr("shutil.which", lambda name: None)
    engine = KiroCliEngine(api_key="kiro-test-123")
    diff = PageDiff(
        page=PageSnapshot(page_id="1", title="t", version=1, body_html="<p>x</p>", url="https://x"),
        previous_version=None, diff_text="spec", is_first_seen=True, body_checksum="abc",
    )
    result = await engine.implement_change(tmp_path, diff, max_turns=5)
    assert result.success is False
    assert "kiro-cli" in result.summary
    assert "not found on PATH" in result.summary


def test_cursor_parse_result_extracts_result_field():
    stdout = '{"result": "Added a cancellation reason field.", "other": 1}'
    assert parse_cursor_result(stdout) == "Added a cancellation reason field."


def test_cursor_parse_result_falls_back_to_raw_text_on_bad_json():
    assert parse_cursor_result("not json") == "not json"


def test_cursor_parse_result_falls_back_when_result_field_missing():
    stdout = '{"other": 1}'
    assert parse_cursor_result(stdout) == stdout


@pytest.mark.parametrize("parse_response", [parse_gemini_response, parse_antigravity_response])
def test_response_field_parsers_extract_response_field(parse_response):
    stdout = '{"response": "Added a cancellation reason field.", "stats": {}}'
    assert parse_response(stdout) == "Added a cancellation reason field."


@pytest.mark.parametrize("parse_response", [parse_gemini_response, parse_antigravity_response])
def test_response_field_parsers_fall_back_to_raw_text_on_bad_json(parse_response):
    assert parse_response("not json") == "not json"


@pytest.mark.parametrize(
    "parse_usage,key",
    [(parse_gemini_usage, "stats"), (parse_antigravity_usage, "stats"), (parse_cursor_usage, "usage")],
)
def test_usage_parsers_extract_dict_field(parse_usage, key):
    stdout = f'{{"response": "done", "{key}": {{"inputTokens": 100, "outputTokens": 50}}}}'
    assert parse_usage(stdout) == {"inputTokens": 100, "outputTokens": 50}


@pytest.mark.parametrize("parse_usage", [parse_gemini_usage, parse_antigravity_usage, parse_cursor_usage])
def test_usage_parsers_return_none_when_absent_or_invalid(parse_usage):
    assert parse_usage('{"response": "done"}') is None
    assert parse_usage("not json") is None


def test_codex_parse_usage_takes_last_usage_event_from_jsonl_stream():
    stdout = "\n".join(
        [
            '{"type": "turn.started"}',
            '{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}',
            '{"type": "item.completed", "item": {"type": "message"}}',
            '{"type": "turn.completed", "usage": {"input_tokens": 40, "output_tokens": 22}}',
        ]
    )
    assert parse_codex_usage(stdout) == {"input_tokens": 40, "output_tokens": 22}


def test_codex_parse_usage_returns_none_when_no_usage_event():
    stdout = '{"type": "turn.started"}\n{"type": "turn.failed", "error": {"message": "boom"}}'
    assert parse_codex_usage(stdout) is None
