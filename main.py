import asyncio
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import flet.fastapi as flet_fastapi
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.config import settings
from core.events import get_gatekeeper_events, get_guardian_events
from core.guardian import approve_rollback, pending_rollbacks
from core.models import AlertPayload, RollbackApproval
from core.auth_gitlab import register_client, get_auth_url, exchange_code_for_token, save_oauth_to_env
from repoguard_agent.agent import invoke_root_agent
from ui import main as flet_main

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await flet_fastapi.app_manager.start()
    yield
    await flet_fastapi.app_manager.shutdown()

app = FastAPI(title="RepoGuard", version="1.0.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_gitlab_token(request: Request) -> None:
    token = request.headers.get("X-Gitlab-Token")
    if token != settings.GITLAB_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook token")


def _verify_alerts_token(request: Request) -> None:
    token = request.headers.get("X-RepoGuard-Token")
    if token != settings.ALERTS_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid alerts token")


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
    if action not in ("open", "reopen", "update"):
        return {"status": "ignored", "reason": f"action '{action}' not handled"}

    # Skip "update" events that are only label changes — those are echoes of our
    # own label application and would cause a feedback loop (repoguard re-analyzes
    # its own label updates in an infinite loop).
    if action == "update":
        changes = payload.get("changes", {})
        if set(changes.keys()) <= {"labels", "updated_at"}:
            return {"status": "ignored", "reason": "label-only update"}

    project_id = payload["project"]["id"]
    mr_iid = attrs["iid"]

    agent_prompt = f"GitLab Merge Request Event: project_id={project_id}, mr_iid={mr_iid}"
    background_tasks.add_task(invoke_root_agent, agent_prompt)
    logger.info("Queued Gatekeeper agent review for MR !%s in project %s", mr_iid, project_id)
    return {"status": "accepted", "mr_iid": mr_iid, "project_id": project_id}


# ---------------------------------------------------------------------------
# Module 2 — Guardian
# ---------------------------------------------------------------------------

SERVICE_URL = "https://repoguard-926140091197.us-central1.run.app"

@app.post("/webhook/alerts")
async def alerts_webhook(request: Request, payload: AlertPayload, background_tasks: BackgroundTasks):
    _verify_alerts_token(request)
    if not settings.GITLAB_PROJECT_ID:
        raise HTTPException(status_code=503, detail="GITLAB_PROJECT_ID not configured")
    base_url = str(request.base_url).rstrip("/")
    
    agent_prompt = f"Production Alert: base_url={base_url}, project_id={settings.GITLAB_PROJECT_ID}, alert_payload={payload.model_dump_json()}"
    background_tasks.add_task(invoke_root_agent, agent_prompt)
    logger.info("Queued Guardian agent remediation for alert: %s at %s", payload.error_type, payload.timestamp)
    return {"status": "accepted", "error_type": payload.error_type}


@app.post("/rollback/approve")
async def rollback_approve(body: RollbackApproval):
    rollback = pending_rollbacks.get(body.token)
    if not rollback:
        raise HTTPException(status_code=404, detail="Token not found or already used")
    result = await approve_rollback(rollback.project_id, body.mr_iid, body.token)
    return {"status": result}


@app.get("/rollback/confirm/{token}/{mr_iid}", response_class=HTMLResponse)
async def rollback_confirm_page(token: str, mr_iid: int):
    rollback = pending_rollbacks.get(token)
    if not rollback or rollback.mr_iid != mr_iid:
        return HTMLResponse(content="""<!DOCTYPE html><html><body style="font-family:system-ui;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0">
<div style="text-align:center;padding:2rem">
  <div style="font-size:3rem">⚠️</div>
  <h2 style="margin:1rem 0">Token not found or already used</h2>
  <p style="color:#64748b">This rollback has already been approved or the token is invalid.</p>
</div></body></html>""", status_code=404)

    commit_short = rollback.commit_sha[:8]
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RepoGuard — Approve Rollback</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}}
  .card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:2.5rem;max-width:480px;width:90%;text-align:center}}
  .icon{{font-size:3rem;margin-bottom:1rem}}
  h1{{font-size:1.4rem;font-weight:700;margin-bottom:0.5rem}}
  .meta{{color:#94a3b8;font-size:0.85rem;margin-bottom:2rem;line-height:1.6}}
  code{{background:#0f172a;padding:2px 6px;border-radius:4px;font-size:0.85em;color:#f472b6}}
  .btn{{display:inline-block;background:#16a34a;color:#fff;border:none;padding:0.85rem 2.5rem;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;width:100%;margin-top:0.5rem}}
  .btn:hover{{background:#15803d}}
  .warning{{background:#7f1d1d;border:1px solid #991b1b;border-radius:8px;padding:0.75rem 1rem;font-size:0.82rem;color:#fca5a5;margin-bottom:1.5rem}}
</style>
</head>
<body>
<div class="card">
  <div class="icon">🚨</div>
  <h1>Approve Emergency Rollback</h1>
  <p class="meta">
    MR <strong>!{mr_iid}</strong> &nbsp;·&nbsp; Commit <code>{commit_short}</code><br>
    Created {rollback.created_at.strftime("%Y-%m-%d %H:%M UTC")}
  </p>
  <div class="warning">⚠️ This will mark the rollback MR as ready to merge. This action cannot be undone.</div>
  <form method="POST" action="/rollback/confirm/{token}/{mr_iid}">
    <button type="submit" class="btn">✅ Approve Rollback</button>
  </form>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.post("/rollback/confirm/{token}/{mr_iid}", response_class=HTMLResponse)
async def rollback_confirm_submit(token: str, mr_iid: int):
    rollback = pending_rollbacks.get(token)
    if not rollback:
        return HTMLResponse(content="<h2>Token not found or already used</h2>", status_code=404)
    result = await approve_rollback(rollback.project_id, mr_iid, token)
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>RepoGuard — Approved</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:system-ui;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}}.card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:2.5rem;max-width:480px;width:90%;text-align:center}}.icon{{font-size:3rem;margin-bottom:1rem}}h1{{font-size:1.4rem;font-weight:700;margin-bottom:0.5rem}}.sub{{color:#94a3b8;font-size:0.85rem;margin-top:0.5rem}}.link{{color:#7dd3fc;text-decoration:none}}</style>
</head>
<body>
<div class="card">
  <div class="icon">✅</div>
  <h1>Rollback Approved</h1>
  <p class="sub">{result}</p>
  <p class="sub" style="margin-top:1.5rem"><a href="/" class="link">← Back to RepoGuard Dashboard</a></p>
</div>
</body>
</html>""")


# ---------------------------------------------------------------------------
# Demo helpers
# ---------------------------------------------------------------------------

BAD_COMMIT_SHA = "e7bebba11332faaf923d766d239e5a189f67229d"

@app.post("/demo/trigger-alert")
async def demo_trigger_alert(request: Request, background_tasks: BackgroundTasks):
    """Fires a pre-canned production alert against the sandbox project. Use for demo recording."""
    _verify_alerts_token(request)
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

    base_url = str(request.base_url).rstrip("/")
    agent_prompt = f"Production Alert: base_url={base_url}, project_id={settings.GITLAB_PROJECT_ID}, alert_payload={payload.model_dump_json()}"
    background_tasks.add_task(invoke_root_agent, agent_prompt)
    return {"status": "demo alert fired", "payload": payload.model_dump()}


@app.get("/health")
async def health():
    return {"status": "ok"}

# --- GitLab OAuth Routes ---

@app.get("/auth/login")
async def auth_login(request: Request):
    """Starts the GitLab OAuth flow, registering the client if needed."""
    redirect_uri = str(request.url_for("auth_callback"))
    
    # Ensure we are registered - DCR is disabled as per hackathon instructions
    # if not settings.GITLAB_CLIENT_ID:
    #     try:
    #         creds = await register_client(redirect_uri)
    #         save_oauth_to_env(client_id=creds["client_id"], client_secret=creds["client_secret"])
    #     except Exception as e:
    #         logger.error("DCR Failed: %s", e)
    #         raise HTTPException(status_code=500, detail=f"Registration failed: {e}")
            
    auth_url = get_auth_url(settings.GITLAB_CLIENT_ID, redirect_uri, state="repoguard-hackathon")
    return {"login_url": auth_url}

@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str):
    """Handles the GitLab OAuth callback."""
    redirect_uri = str(request.url_for("auth_callback"))
    
    try:
        token_data = await exchange_code_for_token(
            settings.GITLAB_CLIENT_ID, 
            settings.GITLAB_CLIENT_SECRET, 
            code, 
            redirect_uri
        )
        save_oauth_to_env(
            access_token=token_data["access_token"], 
            refresh_token=token_data.get("refresh_token", "")
        )
        return HTMLResponse(content="<h1>Authenticated Successfully!</h1><p>You can close this window and return to RepoGuard.</p>")
    except Exception as e:
        logger.error("Token Exchange Failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Token exchange failed: {e}")

app.mount("/", flet_fastapi.app(flet_main))

