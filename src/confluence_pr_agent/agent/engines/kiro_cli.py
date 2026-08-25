"""ChangeEngine backed by AWS's Kiro CLI (`kiro-cli`).

Requires the `kiro-cli` binary on PATH (install:
curl -fsSL https://cli.kiro.dev/install | bash) and KIRO_API_KEY. Flags and
invocation shape are sourced from Kiro's own headless-mode docs
(https://kiro.dev/docs/cli/headless/, https://kiro.dev/changelog/cli/2-0/) --
`kiro-cli chat --no-interactive --trust-all-tools "<prompt>"` -- rather than
verified against a live run of the CLI itself, unlike the other engines in
this directory; the "not found on PATH" error path and the summary this
returns are the same either way, so a flag mismatch surfaces immediately as
a clear failure the first time this engine actually runs, not silently.

No JSON output format or token/cost usage field is documented for headless
mode (unlike claude_code/cursor/gemini), so stdout is treated as the plain
response text and usage is always None -- same "None means the engine
didn't report anything parseable" convention every other engine here
already uses. No native turn-limit concept either, so max_turns becomes a
wall-clock timeout, same as cursor/codex/gemini/antigravity.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from confluence_pr_agent.agent.engines._subprocess_utils import (
    EngineTimeoutError,
    run_cli,
    turns_to_timeout_seconds,
)
from confluence_pr_agent.agent.prompts import build_combined_prompt
from confluence_pr_agent.models import ChangeAgentResult, PageDiff

CLI_BINARY = "kiro-cli"


class KiroCliEngine:
    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    async def implement_change(
        self, repo_dir: Path, diff: PageDiff, max_turns: int, retry_context: str | None = None
    ) -> ChangeAgentResult:
        if shutil.which(CLI_BINARY) is None:
            return ChangeAgentResult(
                success=False,
                summary=(
                    f"'{CLI_BINARY}' (Kiro CLI) not found on PATH. "
                    "Install: curl -fsSL https://cli.kiro.dev/install | bash"
                ),
            )

        prompt = build_combined_prompt(diff, retry_context)
        args = [CLI_BINARY, "chat", "--no-interactive", "--trust-all-tools", prompt]
        timeout = turns_to_timeout_seconds(max_turns)
        extra_env = {"KIRO_API_KEY": self._api_key} if self._api_key else None

        try:
            returncode, stdout, stderr = await run_cli(
                args, cwd=repo_dir, timeout_seconds=timeout, extra_env=extra_env
            )
        except EngineTimeoutError as exc:
            return ChangeAgentResult(success=False, summary=str(exc))

        return ChangeAgentResult(
            success=returncode == 0,
            summary=stdout.strip() or "(kiro-cli produced no output)",
            raw_log=f"stdout:\n{stdout}\n\nstderr:\n{stderr}",
            usage=None,
        )
