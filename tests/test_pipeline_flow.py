from __future__ import annotations

from confluence_pr_agent.pipeline.stages import STAGE_KEYS
from confluence_pr_agent.ui.pipeline_flow import build_flow_steps, split_next_steps


def test_split_next_steps_extracts_a_markdown_heading():
    summary = "I did the thing.\n\n### Next steps\nAdd the `repo-ui` label and re-run."
    body, next_steps = split_next_steps(summary)
    assert body == "I did the thing."
    assert next_steps == "Add the `repo-ui` label and re-run."


def test_split_next_steps_is_case_insensitive_and_heading_style_agnostic():
    for heading in ["Next steps", "NEXT STEPS", "next steps:", "**Next Steps**"]:
        summary = f"Body text.\n\n{heading}\nDo the thing."
        body, next_steps = split_next_steps(summary)
        assert body == "Body text."
        assert next_steps == "Do the thing."


def test_split_next_steps_returns_none_when_no_heading_present():
    summary = "Just a plain summary with no next steps section."
    body, next_steps = split_next_steps(summary)
    assert body == summary
    assert next_steps is None


def _run(**overrides) -> dict:
    base = {
        "status": "running",
        "current_stage": "fetch_page",
        "engine": "gemini",
        "target_repo": "acme/widgets",
        "page_id": "1",
        "page_title": "Spec",
        "files_changed": [],
        "pr_number": None,
        "email_sent": False,
        "email_error": None,
        "judge_verdict": None,
        "judge_reasoning": None,
        "judge_concerns": [],
    }
    base.update(overrides)
    return base


def test_running_marks_reached_stages_done_current_active_rest_pending():
    steps = build_flow_steps(_run(status="running", current_stage="ai_agent"))
    states = {s["key"]: s["state"] for s in steps}

    assert states["fetch_page"] == "done"
    assert states["clone_repo"] == "done"
    assert states["ai_agent"] == "active"
    assert states["run_tests"] == "pending"
    assert states["open_pr"] == "pending"
    assert states["send_email"] == "pending"


def test_no_change_detected_marks_only_fetch_page_done_rest_skipped():
    steps = build_flow_steps(_run(status="no_change_detected", current_stage="fetch_page"))
    states = {s["key"]: s["state"] for s in steps}

    assert states["fetch_page"] == "done"
    for key in STAGE_KEYS[1:]:
        assert states[key] == "skipped"


def test_ignored_marks_only_fetch_page_done_rest_skipped():
    steps = build_flow_steps(_run(status="ignored", current_stage="fetch_page"))
    states = {s["key"]: s["state"] for s in steps}

    assert states["fetch_page"] == "done"
    for key in STAGE_KEYS[1:]:
        assert states[key] == "skipped"


def test_error_at_ai_agent_marks_earlier_stages_done_that_stage_failed_rest_skipped():
    steps = build_flow_steps(_run(status="error", current_stage="ai_agent"))
    states = {s["key"]: s["state"] for s in steps}

    assert states["fetch_page"] == "done"
    assert states["clone_repo"] == "done"
    assert states["ai_agent"] == "failed"
    assert states["run_tests"] == "skipped"
    assert states["open_pr"] == "skipped"
    assert states["send_email"] == "skipped"


def test_tests_failed_marks_run_tests_failed_and_later_stages_skipped():
    steps = build_flow_steps(_run(status="tests_failed", current_stage="run_tests"))
    states = {s["key"]: s["state"] for s in steps}

    assert states["ai_agent"] == "done"
    assert states["run_tests"] == "failed"
    assert states["open_pr"] == "skipped"
    assert states["send_email"] == "skipped"


def test_opened_pr_marks_everything_done_including_email_when_sent():
    steps = build_flow_steps(
        _run(
            status="opened_pr",
            current_stage="send_email",
            email_sent=True,
            pr_number=7,
            judge_verdict="approved",
        )
    )
    states = {s["key"]: s["state"] for s in steps}

    # No jira_issue_key on this run (JIRA_ENABLED off), so that stage reads
    # "skipped" like llm_judge/send_email do when they didn't run either.
    for key in STAGE_KEYS:
        if key == "create_jira_story":
            assert states[key] == "skipped"
        else:
            assert states[key] == "done"


def test_opened_pr_marks_jira_story_done_when_an_issue_was_created():
    steps = build_flow_steps(
        _run(
            status="opened_pr",
            current_stage="send_email",
            email_sent=True,
            pr_number=7,
            judge_verdict="approved",
            jira_issue_key="SD-42",
        )
    )
    states = {s["key"]: s["state"] for s in steps}

    assert states["create_jira_story"] == "done"


def test_opened_pr_marks_email_skipped_when_not_sent():
    steps = build_flow_steps(
        _run(status="opened_pr", current_stage="send_email", email_sent=False, email_error="not configured")
    )
    states = {s["key"]: s["state"] for s in steps}

    assert states["open_pr"] == "done"
    assert states["send_email"] == "skipped"


def test_opened_pr_marks_judge_skipped_when_no_verdict_recorded():
    """Judge disabled, or skipped for missing config -- either way there's no
    judge_verdict in the run, and the step should read as skipped rather than
    falsely "done" (which the naive "everything before send_email is done"
    rule would otherwise say for a status="opened_pr" run).
    """
    steps = build_flow_steps(
        _run(status="opened_pr", current_stage="send_email", email_sent=True, judge_verdict=None)
    )
    states = {s["key"]: s["state"] for s in steps}

    assert states["llm_judge"] == "skipped"
    assert states["open_pr"] == "done"


def test_judge_rejected_marks_llm_judge_failed_but_open_pr_done_and_email_skipped():
    """The judge is advisory, not a gate (orchestrator.py) -- a rejection
    still opens a flagged draft PR, so open_pr must read "done" here, not
    "skipped" the way it does for a real blocking failure (tests_failed).
    Only the summary email is intentionally skipped for a rejected run.
    """
    steps = build_flow_steps(
        _run(status="judge_rejected", current_stage="open_pr", judge_verdict="rejected", pr_number=9, pr_draft=True)
    )
    states = {s["key"]: s["state"] for s in steps}

    assert states["run_tests"] == "done"
    assert states["llm_judge"] == "failed"
    assert states["open_pr"] == "done"
    assert states["send_email"] == "skipped"


def test_opened_pr_marks_llm_judge_warning_for_approved_with_warnings():
    steps = build_flow_steps(
        _run(status="opened_pr", current_stage="send_email", email_sent=True, judge_verdict="approved_with_warnings")
    )
    states = {s["key"]: s["state"] for s in steps}

    assert states["llm_judge"] == "warning"


def test_details_reflect_engine_files_and_pr_number():
    steps = build_flow_steps(
        _run(
            status="opened_pr",
            current_stage="send_email",
            engine="codex",
            files_changed=["a.py", "b.py"],
            pr_number=3,
            email_sent=True,
        )
    )
    by_key = {s["key"]: s for s in steps}

    assert "codex" in by_key["ai_agent"]["detail"]
    assert "2 files" in by_key["ai_agent"]["detail"]
    assert by_key["open_pr"]["detail"] == "#3"
    assert by_key["send_email"]["detail"] == "sent"


def test_details_reflect_draft_pr_and_multiple_attempts():
    steps = build_flow_steps(
        _run(
            status="judge_rejected",
            current_stage="open_pr",
            judge_verdict="rejected",
            pr_number=9,
            pr_draft=True,
            attempts=2,
        )
    )
    by_key = {s["key"]: s for s in steps}

    assert by_key["open_pr"]["detail"] == "#9 (draft)"
    assert "2 attempts" in by_key["ai_agent"]["detail"]


def test_details_reflect_reused_pr():
    steps = build_flow_steps(
        _run(status="opened_pr", current_stage="send_email", email_sent=True, pr_number=9, reused_pr=True)
    )
    by_key = {s["key"]: s for s in steps}

    assert by_key["open_pr"]["detail"] == "#9 (updated)"
