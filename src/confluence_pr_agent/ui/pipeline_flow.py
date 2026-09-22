"""Turns a run record into a stepper diagram: the same component serves as
a live progress indicator while a run's status is "running" and as a
retrospective "how was this built" summary once it's finished -- both are
just different snapshots of the same pipeline/stages.py sequence.
"""

from __future__ import annotations

import re

from confluence_pr_agent.pipeline.stages import STAGE_KEYS, STAGE_LABELS

_NEXT_STEPS_HEADING = re.compile(r"(?:^|\n)\s*#{0,6}\s*\*{0,2}next steps\*{0,2}\s*:?\s*(?:\n|$)", re.IGNORECASE)


def split_next_steps(summary: str) -> tuple[str, str | None]:
    """agent/prompts.py::build_repo_context instructs the agent to end its
    summary with a heading-styled "Next steps" section, precisely when it
    judged the change needs a repo outside what was checked out (the same
    signal RunRecord.flagged_scope_gap is set from -- see orchestrator.py).
    Split it out so run_detail.html can give it the same visual treatment
    as the "more action needed" badge, instead of it reading as an
    undifferentiated tail of a wall of prose in the Summary section.
    """
    match = _NEXT_STEPS_HEADING.search(summary)
    if not match:
        return summary, None
    body = summary[: match.start()].rstrip()
    next_steps = summary[match.end() :].strip()
    return body, (next_steps or None)


def _detail_for(key: str, run: dict) -> str | None:
    if key == "fetch_page":
        return run.get("page_title") or run.get("page_id") or None
    if key == "create_jira_story":
        issue_key = run.get("jira_issue_key")
        if not issue_key:
            return None
        return f"{issue_key} (existing)" if run.get("jira_reused") else issue_key
    if key == "clone_repo":
        return run.get("target_repo") or None
    if key == "ai_agent":
        parts = []
        if run.get("engine"):
            parts.append(run["engine"])
        n = len(run.get("files_changed") or [])
        if n:
            parts.append(f"{n} file{'s' if n != 1 else ''} changed")
        attempts = run.get("attempts") or 1
        if attempts > 1:
            parts.append(f"{attempts} attempts")
        return " · ".join(parts) if parts else None
    if key == "run_tests":
        return None  # pass/fail communicated by the step's color state, not text
    if key == "llm_judge":
        verdict = run.get("judge_verdict")
        if verdict == "approved_with_warnings":
            return "Approved (warnings)"
        return verdict.capitalize() if verdict else None
    if key == "open_pr":
        if not run.get("pr_number"):
            return None
        label = f"#{run['pr_number']}"
        if run.get("pr_draft"):
            label += " (draft)"
        if run.get("reused_pr"):
            label += " (updated)"
        return label
    if key == "send_email":
        if run.get("email_sent"):
            return "sent"
        if run.get("email_error"):
            return "not sent"
        return None
    return None


def build_flow_steps(run: dict) -> list[dict]:
    status = run.get("status")
    current_stage = run.get("current_stage") or STAGE_KEYS[0]
    current_index = STAGE_KEYS.index(current_stage) if current_stage in STAGE_KEYS else 0
    judge_verdict = run.get("judge_verdict")
    # JIRA_ENABLED (or a failed/skipped story creation) means no issue was
    # ever created for this run -- treated the same as llm_judge/send_email
    # not running: "skipped", not "done", regardless of stage position.
    jira_done = bool(run.get("jira_issue_key"))

    steps = []
    for i, key in enumerate(STAGE_KEYS):
        if status == "running":
            state = "done" if i < current_index else "active" if i == current_index else "pending"
        elif status == "judge_rejected":
            # The judge is advisory, not a gate (pipeline/orchestrator.py) --
            # a rejection still opens a flagged draft PR, so unlike
            # error/tests_failed, open_pr reads "done" here, not "skipped".
            if key == "llm_judge":
                state = "failed"
            elif key == "open_pr":
                state = "done"
            elif key == "send_email":
                state = "skipped"  # rejected runs don't send the summary email
            elif key == "create_jira_story":
                state = "done" if jira_done else "skipped"
            else:
                state = "done"
        elif status in ("error", "tests_failed"):
            if key == "create_jira_story" and i < current_index:
                state = "done" if jira_done else "skipped"
            else:
                state = "done" if i < current_index else "failed" if i == current_index else "skipped"
        elif status in ("no_change_detected", "ignored"):
            state = "done" if i <= current_index else "skipped"
        elif status == "opened_pr":
            if key == "llm_judge":
                if judge_verdict == "approved_with_warnings":
                    state = "warning"
                elif judge_verdict:
                    state = "done"
                else:
                    state = "skipped"
            elif key == "send_email":
                state = "done" if run.get("email_sent") else "skipped"
            elif key == "create_jira_story":
                state = "done" if jira_done else "skipped"
            else:
                state = "done"
        else:
            state = "pending"
        steps.append({"key": key, "label": STAGE_LABELS[key], "detail": _detail_for(key, run), "state": state})
    return steps
