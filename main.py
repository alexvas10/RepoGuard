import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from core.config import settings
from core.gatekeeper import process_mr
from core.guardian import approve_rollback, pending_rollbacks, process_alert
from core.models import AlertPayload, RollbackApproval

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="RepoGuard", version="1.0.0")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_gitlab_token(request: Request) -> None:
    token = request.headers.get("X-Gitlab-Token")
    if token != settings.GITLAB_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook token")


# ---------------------------------------------------------------------------
# Module 1 — Gatekeeper
# ---------------------------------------------------------------------------

@app.post("/webhook/gitlab")
async def gitlab_webhook(request: Request, background_tasks: BackgroundTasks):
    _verify_gitlab_token(request)
    payload = await request.json()

    if payload.get("object_kind") != "merge_request":
        return {"status": "ignored", "reason": "not a merge_request event"}

    attrs = payload.get("object_attributes", {})
    action = attrs.get("action")
    if action not in ("open", "update"):
        return {"status": "ignored", "reason": f"action '{action}' not handled"}

    project_id = payload["project"]["id"]
    mr_iid = attrs["iid"]

    background_tasks.add_task(process_mr, project_id, mr_iid)
    logger.info("Queued Gatekeeper analysis for MR !%s in project %s", mr_iid, project_id)
    return {"status": "accepted", "mr_iid": mr_iid, "project_id": project_id}


# ---------------------------------------------------------------------------
# Module 2 — Guardian
# ---------------------------------------------------------------------------

@app.post("/webhook/alerts")
async def alerts_webhook(payload: AlertPayload, background_tasks: BackgroundTasks):
    if not settings.GITLAB_PROJECT_ID:
        raise HTTPException(status_code=503, detail="GITLAB_PROJECT_ID not configured")
    background_tasks.add_task(process_alert, settings.GITLAB_PROJECT_ID, payload)
    logger.info("Queued Guardian analysis for alert: %s at %s", payload.error_type, payload.timestamp)
    return {"status": "accepted", "error_type": payload.error_type}


@app.post("/rollback/approve")
async def rollback_approve(body: RollbackApproval):
    rollback = pending_rollbacks.get(body.token)
    if not rollback:
        raise HTTPException(status_code=404, detail="Token not found or already used")
    result = await approve_rollback(rollback.project_id, body.mr_iid, body.token)
    return {"status": result}


# ---------------------------------------------------------------------------
# Demo helpers
# ---------------------------------------------------------------------------

@app.post("/demo/trigger-alert")
async def demo_trigger_alert(background_tasks: BackgroundTasks):
    """Fires a pre-canned production alert against the sandbox project. Use for demo recording."""
    if not settings.GITLAB_PROJECT_ID:
        raise HTTPException(status_code=503, detail="GITLAB_PROJECT_ID not configured in settings")

    payload = AlertPayload(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        error_type="ZeroDivisionError",
        severity="CRITICAL",
        service="api-server",
        stack_trace=(
            "Traceback (most recent call last):\n"
            "  File 'app/routes/calculate.py', line 42, in divide\n"
            "    return a / b\n"
            "ZeroDivisionError: division by zero"
        ),
    )

    background_tasks.add_task(process_alert, settings.GITLAB_PROJECT_ID, payload)
    return {"status": "demo alert fired", "payload": payload.model_dump()}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/pending-rollbacks")
async def list_pending_rollbacks():
    return {
        token: {
            "project_id": r.project_id,
            "mr_iid": r.mr_iid,
            "commit_sha": r.commit_sha,
            "created_at": r.created_at.isoformat(),
        }
        for token, r in pending_rollbacks.items()
    }
