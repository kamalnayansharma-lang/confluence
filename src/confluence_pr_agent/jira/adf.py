"""Atlassian Document Format helpers.

Jira Cloud's REST API v3 requires `description`/comment bodies as ADF (a
structured JSON document), not plain strings -- unlike Confluence's storage
format, there's no plain-text shortcut. These are minimal, hand-rolled
builders (paragraphs, a heading, a bullet list) good enough for
LLM-generated prose, not a full markdown-to-ADF converter.
"""

from __future__ import annotations


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}] if text else []}


def text_to_adf(text: str) -> dict:
    """One paragraph node per blank-line-separated block."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    content = [_paragraph(p) for p in paragraphs] or [_paragraph("")]
    return {"type": "doc", "version": 1, "content": content}


def _bullet_list(lines: list[str]) -> dict:
    return {"type": "bulletList", "content": [{"type": "listItem", "content": [_paragraph(line)]} for line in lines]}


def build_story_description_adf(
    description: str,
    acceptance_criteria: list[str],
    plan_summary: list[str] | None = None,
    file_changes_by_repo: "dict[str, list[dict]] | None" = None,
) -> dict:
    """`plan_summary`: see ui/plan_sprint.py::_build_plan_summary_lines --
    repo scope, sprint position, depends-on/blocks, why, cross-repo impact.
    `file_changes_by_repo`: repo_label -> [{file_path, change, is_new_file}],
    from ui/plan_sprint.py::_group_file_changes -- rendered as one heading
    per repo (matching how a reviewer actually wants to scan it: "what's
    changing in repo-api" as one block, not interleaved with repo-ui's
    items) followed by that repo's own bullet list. Jira Cloud auto-links a
    bare "KAN-21"-shaped token in plain text to that issue on its own, so
    plan_summary's dependency references don't need explicit ADF link marks
    to be clickable in the Jira UI.
    """
    content = [_paragraph(p.strip()) for p in description.split("\n\n") if p.strip()]

    if acceptance_criteria:
        content.append(
            {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "Acceptance Criteria"}]}
        )
        content.append(_bullet_list(acceptance_criteria))

    if plan_summary or file_changes_by_repo:
        content.append(
            {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "Implementation Plan"}]}
        )
        content.append(
            _paragraph(
                "Best-effort -- predicted from the spec text and each repo's file layout where available, "
                "not verified against real file contents."
            )
        )
        if plan_summary:
            content.append(_bullet_list(plan_summary))
        for repo_label, changes in (file_changes_by_repo or {}).items():
            content.append(
                {"type": "heading", "attrs": {"level": 4}, "content": [{"type": "text", "text": repo_label}]}
            )
            content.append(
                _bullet_list(
                    [
                        f"{fc['file_path']}{' (new file)' if fc.get('is_new_file') else ''} -- {fc['change']}"
                        for fc in changes
                    ]
                )
            )

    return {"type": "doc", "version": 1, "content": content or [_paragraph("")]}
