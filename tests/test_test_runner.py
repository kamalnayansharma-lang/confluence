from __future__ import annotations

import sys

from confluence_pr_agent.testing.test_runner import run_tests


async def test_run_tests_installs_requirements_before_running_command(tmp_path):
    (tmp_path / "requirements.txt").write_text("")  # empty on purpose: install should still run, just do nothing
    (tmp_path / "test_something.py").write_text("def test_ok():\n    assert True\n")

    result = await run_tests(tmp_path, f"{sys.executable} -m pytest -q")

    assert result.passed is True
    assert "pip install -r requirements.txt" in result.output


async def test_run_tests_skips_install_when_no_requirements_file(tmp_path):
    (tmp_path / "test_something.py").write_text("def test_ok():\n    assert True\n")

    result = await run_tests(tmp_path, f"{sys.executable} -m pytest -q")

    assert result.passed is True
    assert "pip install" not in result.output


async def test_run_tests_reports_failure_and_includes_output(tmp_path):
    (tmp_path / "test_something.py").write_text("def test_fail():\n    assert False, 'boom'\n")

    result = await run_tests(tmp_path, f"{sys.executable} -m pytest -q")

    assert result.passed is False
    assert "boom" in result.output


async def test_run_tests_reports_a_clean_failure_for_a_nonexistent_binary(tmp_path):
    """Regression test: this used to raise an unhandled FileNotFoundError
    that crashed the whole pipeline run (see pipeline/orchestrator.py's
    lint-gate fail-open handling, which depends on crashed=True being set
    cleanly here instead)."""
    result = await run_tests(tmp_path, "this-binary-definitely-does-not-exist-anywhere")

    assert result.passed is False
    assert result.crashed is True
    assert "Could not run this command" in result.output


async def test_run_tests_reports_a_clean_failure_for_malformed_shell_quoting(tmp_path):
    """shlex.split raises ValueError on an unclosed quote -- must not
    propagate as an unhandled exception either."""
    result = await run_tests(tmp_path, 'ruff check "unclosed')

    assert result.passed is False
    assert result.crashed is True
    assert "Could not run this command" in result.output


async def test_run_tests_normal_failure_is_not_flagged_as_crashed(tmp_path):
    """A real test failure (the binary exists, ran, and genuinely failed)
    must NOT be treated as crashed=True -- only "couldn't run it at all"
    gets that flag, so a real lint/test violation still blocks the run."""
    (tmp_path / "test_something.py").write_text("def test_fail():\n    assert False\n")

    result = await run_tests(tmp_path, f"{sys.executable} -m pytest -q")

    assert result.passed is False
    assert result.crashed is False
