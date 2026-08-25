""""Plan a Sprint" -- search a batch of BRD+sprint-tagged Confluence pages,
have jira/sprint_planner.py propose routing labels + a dependency order,
show any missing-label gaps *before* anything is implemented, then let a
human Confirm (writes Jira stories + issue links) and Approve (per page,
drives the same Jira transition pipeline/approval_poller.py already
understands).

Deliberately three separate actions, not one: Plan is a dry run (nothing
written to Jira); Confirm is the first point anything durable/visible to a
team gets created; Approve is what actually lets pipeline/sprint_runner.py
(Phase 3) touch a repo. See storage/sprint_plan_store.py for the phases each
page moves through.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from confluence_pr_agent.confluence.client import ConfluenceClient
from confluence_pr_agent.confluence.diff import build_full_spec_diff
from confluence_pr_agent.config import get_settings
from confluence_pr_agent.jira.client import JiraClient
from confluence_pr_agent.jira.sprint_dependency import DependencyCycleError, topological_order
from confluence_pr_agent.jira.sprint_planner import generate_sprint_plan
from confluence_pr_agent.jira.story_writer import generate_story_content
from confluence_pr_agent.models import JiraIssueResult, PageDiff, PageSnapshot
from confluence_pr_agent.storage.page_store import PageStore
from confluence_pr_agent.storage.sprint_plan_store import SprintPlan, SprintPlanPage, SprintPlanStore
from confluence_pr_agent.ui.auth import current_username

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _clients(settings) -> tuple[ConfluenceClient, JiraClient, PageStore, SprintPlanStore]:
    return (
        ConfluenceClient(settings.confluence_base_url, settings.confluence_user_email, settings.confluence_api_token),
        JiraClient(settings.jira_base_url, settings.jira_user_email, settings.jira_api_token),
        PageStore(settings.page_store_path),
        SprintPlanStore(settings.sprint_plan_store_path),
    )


_PAGE_ID_TOKEN = re.compile(r"\b\d{4,}\b")


def _linkify_page_ids(text: str, pages_by_id: dict[str, SprintPlanPage]) -> str:
    """jira/sprint_planner.py's dependency_rationale is free-form LLM text
    that sometimes cites a bare page id (e.g. "this page depends on 5275661
    because..."). Wraps any such token in a link to that page's real
    Confluence URL -- but only when the digits match a page actually in
    this plan, never an arbitrary number, so LLM output can't be used to
    fabricate a link to somewhere unexpected.

    Escapes `text` itself first (it's LLM output, never trusted as HTML),
    then injects anchor tags around already-escaped digit runs -- the
    result is marked `| safe` in the template, so this function is the one
    place responsible for making sure nothing except the anchors it builds
    itself is real HTML.
    """
    if not text:
        return text
    escaped = html.escape(text)

    def _replace(match: re.Match) -> str:
        token = match.group(0)
        page = pages_by_id.get(token)
        if page is None:
            return token
        url = html.escape(page["page_url"], quote=True)
        title = html.escape(page["page_title"], quote=True)
        return f'<a href="{url}" title="{title}" target="_blank" rel="noopener">{token}</a>'

    return _PAGE_ID_TOKEN.sub(_replace, escaped)


def _page_diff(sp: SprintPlanPage) -> PageDiff:
    """Reconstructs a PageDiff from a stored SprintPlanPage -- same
    "reconstruct from the saved snapshot, never re-fetch" idiom
    pipeline/orchestrator.py's `resume` path uses for PendingApproval.
    """
    page = PageSnapshot(
        page_id=sp["page_id"],
        title=sp["page_title"],
        version=sp["page_version"],
        body_html=sp["page_body_html"],
        url=sp["page_url"],
        labels=sp["page_labels"],
    )
    return PageDiff(
        page=page,
        previous_version=sp["previous_version"],
        diff_text=sp["diff_text"],
        is_first_seen=sp["is_first_seen"],
        body_checksum=sp["body_checksum"],
    )


@router.get("/ui/plan-sprint")
async def plan_sprint_index(request: Request, username: str = Depends(current_username)):
    settings = get_settings(username)
    _, _, _, plan_store = _clients(settings)
    return templates.TemplateResponse(
        request,
        "plan_sprint_index.html",
        {
            "plans": sorted(plan_store.list_all(), key=lambda p: p["created_at"], reverse=True),
            "gate_labels": settings.confluence_allowed_labels_list,
            "approved_status_name": settings.jira_approved_status_name,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/ui/plan-sprint/plan")
async def plan_sprint_run(request: Request, sprint_tag: str = Form(...), username: str = Depends(current_username)):
    settings = get_settings(username)
    sprint_tag = sprint_tag.strip()
    if not sprint_tag:
        return RedirectResponse(url="/ui/plan-sprint?error=Enter a sprint label.", status_code=303)

    confluence, jira, page_store, plan_store = _clients(settings)
    try:
        gate_labels = settings.confluence_allowed_labels_list
        # Only the first configured gate label is ANDed in here -- see
        # module docstring's note on CONFLUENCE_ALLOWED_LABELS being an
        # OR-list itself, which AND-ing in full would over-constrain.
        required = [gate_labels[0], sprint_tag] if gate_labels else [sprint_tag]
        page_ids = await confluence.search_page_ids_all_labels(settings.confluence_space_key, required)
        if not page_ids:
            return RedirectResponse(
                url=f"/ui/plan-sprint?error=No pages found carrying both {' + '.join(required)!r}.",
                status_code=303,
            )

        repo_targets = settings.resolved_repo_targets
        pages_by_id: dict[str, PageSnapshot] = {}
        diffs: list[PageDiff] = []
        for page_id in page_ids:
            page = await confluence.fetch_page(page_id)
            pages_by_id[page_id] = page
            # Full current spec, not an incremental diff against whatever
            # the regular single-page pipeline last processed -- see
            # build_full_spec_diff's docstring. page_store is still used
            # elsewhere below (Confirm's story-reuse check), just not here.
            diffs.append(build_full_spec_diff(page))

        plan_content = await generate_sprint_plan(settings, diffs, repo_targets)
        predictions = {p.page_id: p for p in plan_content.pages}

        depends_on = {
            page_id: [d for d in (predictions[page_id].depends_on_page_ids if page_id in predictions else []) if d in pages_by_id]
            for page_id in pages_by_id
        }
        cycle_error: str | None = None
        try:
            order = topological_order(depends_on)
        except DependencyCycleError as exc:
            cycle_error = (
                f"The planner proposed a dependency cycle ({' -> '.join(exc.cycle)}); review and re-plan "
                "after adjusting the spec text, or confirm anyway and reorder manually in Jira."
            )
            order = list(pages_by_id.keys())

        plan_pages: list[SprintPlanPage] = []
        for page_id, page in pages_by_id.items():
            diff = next(d for d in diffs if d.page.page_id == page_id)
            prediction = predictions.get(page_id)
            predicted_labels = prediction.predicted_labels if prediction else []
            applied_lower = {lbl.lower() for lbl in page.labels}
            label_gap = [lbl for lbl in predicted_labels if lbl.lower() not in applied_lower]
            plan_pages.append(
                SprintPlanPage(
                    page_id=page_id,
                    page_title=page.title,
                    page_url=page.url,
                    page_version=page.version,
                    page_body_html=page.body_html,
                    page_labels=page.labels,
                    previous_version=diff.previous_version,
                    diff_text=diff.diff_text,
                    is_first_seen=diff.is_first_seen,
                    body_checksum=diff.body_checksum,
                    predicted_labels=predicted_labels,
                    applied_labels=page.labels,
                    label_gap=label_gap,
                    depends_on_page_ids=prediction.depends_on_page_ids if prediction else [],
                    dependency_rationale=prediction.dependency_rationale if prediction else "",
                    phase="planned",
                )
            )

        plan_store.put(
            SprintPlan(
                sprint_tag=sprint_tag,
                gate_label=required[0] if len(required) > 1 else "",
                space_key=settings.confluence_space_key,
                created_at=datetime.now(timezone.utc).isoformat(),
                pages=plan_pages,
                order=order,
            )
        )
    except Exception as exc:
        logger.exception("Plan a Sprint failed for tag %s", sprint_tag)
        return RedirectResponse(url=f"/ui/plan-sprint?error=Planning failed: {exc}", status_code=303)
    finally:
        await confluence.aclose()
        await jira.aclose()

    url = f"/ui/plan-sprint/{sprint_tag}"
    if cycle_error:
        url += f"?error={cycle_error}"
    return RedirectResponse(url=url, status_code=303)


@router.get("/ui/plan-sprint/{sprint_tag}")
async def plan_sprint_detail(request: Request, sprint_tag: str, username: str = Depends(current_username)):
    settings = get_settings(username)
    _, _, _, plan_store = _clients(settings)
    plan = plan_store.get(sprint_tag)
    if plan is None:
        return RedirectResponse(url="/ui/plan-sprint?error=No such plan.", status_code=303)

    pages_by_id = {p["page_id"]: p for p in plan["pages"]}
    ordered_pages = [pages_by_id[pid] for pid in plan["order"] if pid in pages_by_id]
    # Any page the order left out (shouldn't happen outside a cycle) still
    # gets shown, at the end, rather than silently dropped from the view.
    ordered_pages.extend(p for p in plan["pages"] if p["page_id"] not in plan["order"])

    # The reverse relation, computed fresh for display -- not persisted --
    # so "which stories does THIS one block" is as visible as "what does
    # this depend on" instead of making a reader mentally invert the column.
    # dependency_rationale_html: see _linkify_page_ids -- turns a bare page
    # id the LLM cited in its own rationale text into a real link.
    ordered_pages = [
        {
            **p,
            "blocks_page_ids": [p2["page_id"] for p2 in plan["pages"] if p["page_id"] in p2["depends_on_page_ids"]],
            "dependency_rationale_html": _linkify_page_ids(p["dependency_rationale"], pages_by_id),
        }
        for p in ordered_pages
    ]

    return templates.TemplateResponse(
        request,
        "plan_sprint_detail.html",
        {
            "plan": plan,
            "pages": ordered_pages,
            "approved_status_name": settings.jira_approved_status_name,
            "error": request.query_params.get("error"),
        },
    )


async def _confirm_one_page(
    settings, jira: JiraClient, page_store: PageStore, sp: SprintPlanPage, sprint_tag: str
) -> str | None:
    """Shared by the "Confirm plan" (all) and "Confirm selected" (batch)
    actions -- creates or reuses this page's Jira story, mutating `sp` in
    place. The caller is responsible for plan_store.put(plan) once, after
    every page in a batch is done. Returns an error message on failure,
    None on success -- never raises, so a batch call can keep going through
    the rest of a selection after one page fails.
    """
    try:
        diff = _page_diff(sp)
        previous = page_store.get(sp["page_id"])
        existing_key = previous.get("jira_issue_key") if previous else None
        jira_issue: JiraIssueResult | None = None
        if existing_key:
            try:
                status = await jira.get_issue_status(existing_key)
                if status.is_open:
                    jira_issue = JiraIssueResult(
                        key=status.key, url=f"{settings.jira_base_url.rstrip('/')}/browse/{status.key}"
                    )
            except Exception as exc:
                logger.warning("Could not check existing story %s for page %s: %s", existing_key, sp["page_id"], exc)

        if jira_issue is None:
            story = await generate_story_content(settings, diff)
            jira_issue = await jira.create_issue(
                project_key=settings.jira_project_key,
                issue_type=settings.jira_issue_type,
                summary=story.summary,
                description=story.description,
                acceptance_criteria=story.acceptance_criteria,
            )
            try:
                await jira.add_comment(
                    jira_issue.key,
                    f"Part of sprint `{sprint_tag}` (planned via Plan a Sprint). Full current spec, "
                    f"as of v{sp['page_version']}:\n\n{sp['diff_text'][:8000]}",
                )
            except Exception as exc:
                logger.warning("Failed to comment spec on new story %s: %s", jira_issue.key, exc)

        sp["jira_issue_key"] = jira_issue.key
        sp["jira_issue_url"] = jira_issue.url
        sp["phase"] = "confirmed"
        page_store.remember_jira_issue(sp["page_id"], jira_issue.key)
        return None
    except Exception as exc:
        return f"{sp['page_title']}: {exc}"


async def _link_confirmed_dependencies(jira: JiraClient, plan: SprintPlan) -> None:
    """Writes "from blocks to" for every proposed edge where both sides
    currently have a story key -- run after every Confirm/Confirm-selected
    call (not just once), so a dependency edge involving a page confirmed
    in an earlier batch still gets linked once its counterpart catches up.
    """
    keys_by_page = {sp["page_id"]: sp.get("jira_issue_key") for sp in plan["pages"]}
    for sp in plan["pages"]:
        from_key = keys_by_page.get(sp["page_id"])
        if not from_key:
            continue
        for dep_page_id in sp["depends_on_page_ids"]:
            to_key = keys_by_page.get(dep_page_id)
            if not to_key or to_key == from_key:
                continue
            try:
                # dep_page_id blocks sp["page_id"] -- the dependency must
                # come first, so it's the inward "Blocks" issue.
                await jira.link_issues(to_key, from_key, link_type="Blocks")
            except Exception as exc:
                logger.warning("Failed to link %s -> %s: %s", to_key, from_key, exc)


@router.post("/ui/plan-sprint/{sprint_tag}/confirm")
async def plan_sprint_confirm(request: Request, sprint_tag: str, username: str = Depends(current_username)):
    settings = get_settings(username)
    confluence, jira, page_store, plan_store = _clients(settings)
    plan = plan_store.get(sprint_tag)
    if plan is None:
        return RedirectResponse(url="/ui/plan-sprint?error=No such plan.", status_code=303)

    errors: list[str] = []
    try:
        for sp in plan["pages"]:
            if sp["phase"] != "planned":
                continue
            error = await _confirm_one_page(settings, jira, page_store, sp, sprint_tag)
            if error:
                errors.append(error)
        await _link_confirmed_dependencies(jira, plan)
        plan_store.put(plan)
    except Exception as exc:
        logger.exception("Confirming sprint plan %s failed", sprint_tag)
        errors.append(str(exc))
    finally:
        await confluence.aclose()
        await jira.aclose()

    url = f"/ui/plan-sprint/{sprint_tag}"
    if errors:
        url += f"?error=Confirm failed for some pages: " + " | ".join(errors)
    return RedirectResponse(url=url, status_code=303)


@router.post("/ui/plan-sprint/{sprint_tag}/confirm-selected")
async def plan_sprint_confirm_selected(
    request: Request, sprint_tag: str, page_ids: list[str] = Form(default=[]), username: str = Depends(current_username)
):
    """The "Confirm selected" master button -- like plan_sprint_confirm but
    scoped to a chosen subset of still-"planned" pages, for reviewing and
    confirming a batch incrementally rather than all-or-nothing.
    """
    settings = get_settings(username)
    confluence, jira, page_store, plan_store = _clients(settings)
    plan = plan_store.get(sprint_tag)
    if plan is None:
        return RedirectResponse(url="/ui/plan-sprint?error=No such plan.", status_code=303)
    if not page_ids:
        return RedirectResponse(url=f"/ui/plan-sprint/{sprint_tag}?error=No stories selected.", status_code=303)

    pages_by_id = {sp["page_id"]: sp for sp in plan["pages"]}
    errors: list[str] = []
    confirmed_count = 0
    try:
        for page_id in page_ids:
            sp = pages_by_id.get(page_id)
            if sp is None:
                errors.append(f"{page_id}: not found in this plan.")
                continue
            if sp["phase"] != "planned":
                errors.append(f"{sp['page_title']}: already {sp['phase']}, not planned.")
                continue
            error = await _confirm_one_page(settings, jira, page_store, sp, sprint_tag)
            if error:
                errors.append(error)
            else:
                confirmed_count += 1
        await _link_confirmed_dependencies(jira, plan)
        plan_store.put(plan)
    except Exception as exc:
        logger.exception("Confirming selected pages in sprint %s failed", sprint_tag)
        errors.append(str(exc))
    finally:
        await confluence.aclose()
        await jira.aclose()

    url = f"/ui/plan-sprint/{sprint_tag}"
    if errors:
        summary = f"Confirmed {confirmed_count}/{len(page_ids)}. Failed: " + " | ".join(errors)
        url += f"?error={summary}"
    return RedirectResponse(url=url, status_code=303)


async def _approve_one_page(jira: JiraClient, plan: SprintPlan, page_id: str, approved_status_name: str) -> str | None:
    """Shared by the per-row and batch Approve actions -- transitions one
    page's Jira story and flips its phase, mutating `plan` in place (the
    caller is responsible for plan_store.put(plan) once, after every page
    in a batch is done, rather than once per page). Returns an error
    message on failure, None on success -- never raises, so a batch call
    can keep going through the rest of a selection after one page fails.
    """
    sp = next((p for p in plan["pages"] if p["page_id"] == page_id), None)
    if sp is None:
        return f"{page_id}: not found in this plan."
    if not sp.get("jira_issue_key"):
        return f"{sp['page_title']}: no Jira story yet -- Confirm the plan first."
    try:
        moved = await jira.transition_issue(sp["jira_issue_key"], approved_status_name)
    except Exception as exc:
        return f"{sp['page_title']} ({sp['jira_issue_key']}): {exc}"
    if not moved:
        return (
            f"{sp['page_title']} ({sp['jira_issue_key']}): Jira has no transition named "
            f"{approved_status_name!r} available from its current status."
        )
    sp["phase"] = "approved"
    return None


@router.post("/ui/plan-sprint/{sprint_tag}/pages/{page_id}/approve")
async def plan_sprint_approve_page(
    request: Request, sprint_tag: str, page_id: str, username: str = Depends(current_username)
):
    settings = get_settings(username)
    confluence, jira, page_store, plan_store = _clients(settings)
    plan = plan_store.get(sprint_tag)
    if plan is None:
        return RedirectResponse(url="/ui/plan-sprint?error=No such plan.", status_code=303)

    if not settings.jira_approved_status_name.strip():
        return RedirectResponse(
            url=f"/ui/plan-sprint/{sprint_tag}?error=Set 'Approved status name' under Jira in Configuration first.",
            status_code=303,
        )

    error = None
    try:
        error = await _approve_one_page(jira, plan, page_id, settings.jira_approved_status_name)
        if error is None:
            plan_store.put(plan)
    except Exception as exc:
        logger.exception("Approving %s in sprint %s failed", page_id, sprint_tag)
        error = f"Approve failed: {exc}"
    finally:
        await confluence.aclose()
        await jira.aclose()

    url = f"/ui/plan-sprint/{sprint_tag}"
    if error:
        url += f"?error={error}"
    return RedirectResponse(url=url, status_code=303)


@router.post("/ui/plan-sprint/{sprint_tag}/approve-selected")
async def plan_sprint_approve_selected(
    request: Request, sprint_tag: str, page_ids: list[str] = Form(default=[]), username: str = Depends(current_username)
):
    """The master Approve button -- one Jira call per selected page,
    sequentially (these are direct API calls, not agent runs, so there's no
    need for the sprint runner's own seriality concerns here). Failures on
    individual pages don't stop the rest of the batch; every failure is
    collected and reported together rather than only ever showing the
    first one.
    """
    settings = get_settings(username)
    confluence, jira, page_store, plan_store = _clients(settings)
    plan = plan_store.get(sprint_tag)
    if plan is None:
        return RedirectResponse(url="/ui/plan-sprint?error=No such plan.", status_code=303)

    if not settings.jira_approved_status_name.strip():
        return RedirectResponse(
            url=f"/ui/plan-sprint/{sprint_tag}?error=Set 'Approved status name' under Jira in Configuration first.",
            status_code=303,
        )
    if not page_ids:
        return RedirectResponse(
            url=f"/ui/plan-sprint/{sprint_tag}?error=No stories selected.", status_code=303
        )

    errors: list[str] = []
    approved_count = 0
    try:
        for page_id in page_ids:
            error = await _approve_one_page(jira, plan, page_id, settings.jira_approved_status_name)
            if error is None:
                approved_count += 1
            else:
                errors.append(error)
        plan_store.put(plan)
    except Exception as exc:
        logger.exception("Batch-approving sprint %s failed", sprint_tag)
        errors.append(str(exc))
    finally:
        await confluence.aclose()
        await jira.aclose()

    url = f"/ui/plan-sprint/{sprint_tag}"
    if errors:
        summary = f"Approved {approved_count}/{len(page_ids)}. Failed: " + " | ".join(errors)
        url += f"?error={summary}"
    return RedirectResponse(url=url, status_code=303)

    url = f"/ui/plan-sprint/{sprint_tag}"
    if error:
        url += f"?error={error}"
    return RedirectResponse(url=url, status_code=303)
