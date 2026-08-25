"""Runs the target repo's test suite and gates PR creation on it passing."""

from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path

from confluence_pr_agent.models import RepoTestResult


async def _run(*args: str, cwd: Path) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    return process.returncode or 0, stdout.decode("utf-8", errors="replace")


async def run_tests(repo_dir: Path, command: str) -> RepoTestResult:
    """Installs the target repo's own dependencies (if a requirements.txt
    exists) before running its test command.

    This used to be skipped, on the assumption the container already had
    whatever the target repo needs. It doesn't -- this service's own image
    only installs *its own* requirements.txt, never the target repo's. The
    only reason this ever worked was that the change engine's own agentic
    loop sometimes ran `pip install` itself as a side effect of verifying
    its own work before finishing, which is not something to depend on: a
    run where the engine didn't happen to do that failed here with plain
    import errors (e.g. "No module named 'django'"), misreported as the
    generated code being broken when it was actually fine.
    """
    log_parts: list[str] = []

    requirements_file = repo_dir / "requirements.txt"
    if requirements_file.exists():
        returncode, install_output = await _run(
            sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt", cwd=repo_dir
        )
        log_parts.append(f"$ pip install -r requirements.txt\n{install_output}")
        if returncode != 0:
            log_parts.append(f"(dependency install failed with exit code {returncode}; running tests anyway)")

    # shlex.split raises ValueError on malformed quoting (e.g. an unclosed
    # quote); create_subprocess_exec raises OSError (FileNotFoundError,
    # PermissionError, NotADirectoryError, ...) when the parsed command's
    # binary can't actually be run. Both are "this command is unusable", not
    # "the tests/lint failed" -- caught here so a typo'd command produces a
    # clean, readable RepoTestResult(crashed=True) instead of an unhandled
    # exception that would otherwise abort the whole pipeline run with a
    # raw traceback as the only explanation.
    try:
        args = shlex.split(command)
        returncode, output = await _run(*args, cwd=repo_dir)
    except (ValueError, OSError) as exc:
        log_parts.append(
            f"$ {command}\nCould not run this command: {exc}\n"
            "Check it's valid shell syntax and its binary actually exists in this container."
        )
        return RepoTestResult(passed=False, crashed=True, output="\n".join(log_parts), command=command)

    log_parts.append(f"$ {command}\n{output}")

    return RepoTestResult(passed=returncode == 0, output="\n".join(log_parts), command=command)
