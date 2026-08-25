"""Picks a ChangeEngine implementation by name (CHANGE_AGENT_ENGINE in .env).

Adding a new engine: implement ChangeEngine (agent/base.py) in
agent/engines/, register it here. Nothing else in the pipeline changes.
"""

from __future__ import annotations

from confluence_pr_agent.agent.base import ChangeEngine
from confluence_pr_agent.config import Settings

_ENGINES = ("claude_code", "cursor", "copilot", "codex", "gemini", "antigravity", "kiro")


def build_change_engine(settings: Settings) -> ChangeEngine:
    normalized = settings.change_agent_engine.strip().lower()

    if normalized == "claude_code":
        from confluence_pr_agent.agent.engines.claude_code import ClaudeCodeEngine

        return ClaudeCodeEngine(api_key=settings.anthropic_api_key)

    if normalized == "cursor":
        from confluence_pr_agent.agent.engines.cursor_cli import CursorCliEngine

        return CursorCliEngine(api_key=settings.cursor_api_key)

    if normalized == "copilot":
        from confluence_pr_agent.agent.engines.copilot_cli import CopilotCliEngine

        return CopilotCliEngine(github_token=settings.github_token)

    if normalized == "codex":
        from confluence_pr_agent.agent.engines.codex_cli import CodexCliEngine

        return CodexCliEngine(api_key=settings.openai_api_key)

    if normalized == "gemini":
        from confluence_pr_agent.agent.engines.gemini_cli import GeminiCliEngine

        return GeminiCliEngine(api_key=settings.gemini_api_key)

    if normalized == "antigravity":
        from confluence_pr_agent.agent.engines.antigravity_cli import AntigravityCliEngine

        return AntigravityCliEngine()

    if normalized == "kiro":
        from confluence_pr_agent.agent.engines.kiro_cli import KiroCliEngine

        return KiroCliEngine(api_key=settings.kiro_api_key)

    raise ValueError(f"Unknown CHANGE_AGENT_ENGINE '{settings.change_agent_engine}' -- expected one of {_ENGINES}")
