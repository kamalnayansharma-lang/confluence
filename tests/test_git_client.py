from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from confluence_pr_agent.repo.git_client import _run


@pytest.mark.asyncio
async def test_run_injects_safe_directory_override(tmp_path: Path, monkeypatch):
    seen = {}

    async def fake_create_subprocess_exec(*args, cwd=None, stdout=None, stderr=None):
        seen["args"] = args
        seen["cwd"] = cwd

        class Completed:
            def __init__(self):
                self.returncode = 0

            async def communicate(self):
                return b"ok\n", None

        return Completed()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await _run("git", "status", cwd=tmp_path)

    assert result == "ok\n"
    assert seen["cwd"] == str(tmp_path)
    assert seen["args"][:4] == ("git", "-c", "safe.directory=*", "status")
