"""FastAPI webhook receiver for Confluence "page updated" events."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from confluence_pr_agent.config import get_process_config, get_settings
from confluence_pr_agent.pipeline.approval_poller import approval_poll_scan_loop
from confluence_pr_agent.pipeline.orchestrator import run_pipeline
from confluence_pr_agent.pipeline.poller import poll_scan_loop
from confluence_pr_agent.pipeline.sprint_runner import sprint_runner_scan_loop
from confluence_pr_agent.ui.plan_sprint import router as plan_sprint_router
from confluence_pr_agent.ui.routes import router as ui_router
from confluence_pr_agent.webhook.schemas import extract_event_type, extract_page_id

logging.basicConfig(level=get_process_config().log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Unconditional -- unlike the old single-tenant poll_loop (gated on one
    # global CONFLUENCE_POLL_ENABLED), poll_scan_loop checks each
    # provisioned user's own setting itself every tick, so there's no
    # process-level flag left to gate startup on. A quiet loop (no users
    # with polling enabled) is cheap: one directory scan every 30s.
    logger.info("Starting the per-user Confluence poll scan loop")
    poll_task = asyncio.create_task(poll_scan_loop())
    logger.info("Starting the per-user JIRA_APPROVAL_REQUIRED scan loop")
    approval_task = asyncio.create_task(approval_poll_scan_loop())
    logger.info("Starting the per-user sprint runner scan loop")
    sprint_task = asyncio.create_task(sprint_runner_scan_loop())
    yield
    poll_task.cancel()
    approval_task.cancel()
    sprint_task.cancel()
    for task in (poll_task, approval_task, sprint_task):
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Driftbridge",
    description="Receives Confluence page-updated webhooks and bridges the drift between spec and code.",
    lifespan=lifespan,
)
app.include_router(ui_router)
app.include_router(plan_sprint_router)


@app.get("/")
async def root() -> RedirectResponse:
    # No route existed for the bare path before this -- Caddy's Basic Auth
    # gate covers "/" too (its catch-all handle block), so a successful
    # login landed on a 404 with nothing else to do. /ui itself already
    # redirects on to /ui/runs (see ui/routes.py::ui_home); this just
    # extends that same landing behavior to the site root.
    return RedirectResponse(url="/ui")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Verify an HMAC-SHA256 signature if a shared secret is configured.

    Verification is skipped (returns True) when no secret is configured --
    fine for local POC use, not recommended for a publicly reachable endpoint.
    """
    if not secret:
        return True
    if not signature_header:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


@app.post("/webhook/confluence")
async def confluence_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    # Deliberately the single bootstrap/default user, not per-caller --
    # unlike /ui/* routes, a raw webhook delivery has no Caddy-authenticated
    # identity attached to it at all (it's verified by HMAC secret instead,
    # see _verify_signature), so there's no "which user" signal to resolve
    # against. See config.py's ProcessConfig.resolved_default_user.
    settings = get_settings(get_process_config().resolved_default_user)
    raw_body = await request.body()

    if not _verify_signature(settings.confluence_webhook_secret, raw_body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    payload = await request.json()
    page_id = extract_page_id(payload)
    event_type = extract_event_type(payload)

    if page_id is None:
        raise HTTPException(status_code=400, detail="could not find a page id in the webhook payload")

    logger.info("Received Confluence webhook event=%s page_id=%s", event_type, page_id)
    # No deps passed -- run_pipeline builds (and, importantly, closes) its
    # own via build_deps(), which itself resolves to this same bootstrap
    # user when given no settings (see orchestrator.py::build_deps). Passing
    # deps built here instead would leave them unclosed: run_pipeline only
    # closes deps it built itself (owns_deps), not ones handed to it.
    background_tasks.add_task(run_pipeline, page_id)

    return {"status": "accepted", "page_id": page_id}


if __name__ == "__main__":
    import uvicorn

    process = get_process_config()
    uvicorn.run(app, host=process.webhook_host, port=process.webhook_port)
