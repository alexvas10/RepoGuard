import asyncio
import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.config import settings
from core.events import get_gatekeeper_events, get_guardian_events
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

    background_tasks.add_task(process_mr, project_id, mr_iid)
    logger.info("Queued Gatekeeper analysis for MR !%s in project %s", mr_iid, project_id)
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
    background_tasks.add_task(process_alert, settings.GITLAB_PROJECT_ID, payload, None, base_url)
    logger.info("Queued Guardian analysis for alert: %s at %s", payload.error_type, payload.timestamp)
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
    background_tasks.add_task(process_alert, settings.GITLAB_PROJECT_ID, payload, BAD_COMMIT_SHA, base_url)
    return {"status": "demo alert fired", "payload": payload.model_dump()}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(verdict: str = "", sort: str = "desc"):
    ascending = sort == "asc"
    gk_events, gd_events = await asyncio.gather(
        get_gatekeeper_events(verdict_filter=verdict or None, ascending=ascending),
        get_guardian_events(ascending=ascending),
    )

    def verdict_badge(v: str) -> str:
        cfg = {
            "APPROVED":     ("✅ APPROVED",     "#166534", "#86efac"),
            "REJECTED":     ("🔴 REJECTED",     "#7f1d1d", "#fca5a5"),
            "NEEDS_REVIEW": ("🟡 NEEDS REVIEW", "#713f12", "#fde68a"),
        }
        label, bg, fg = cfg.get(v.upper(), (v, "#1e293b", "#94a3b8"))
        return (f'<span style="background:{bg};color:{fg};padding:2px 10px;'
                f'border-radius:999px;font-size:0.75rem;font-weight:600">{label}</span>')

    def status_badge(s: str) -> str:
        bg, fg = ("#0c4a6e", "#7dd3fc") if "approved" in s else ("#4a1d96", "#c4b5fd")
        return (f'<span style="background:{bg};color:{fg};padding:2px 10px;'
                f'border-radius:999px;font-size:0.75rem;font-weight:600">{s}</span>')

    def filter_link(label: str, v: str) -> str:
        active = (v == verdict)
        bg = "#3b82f6" if active else "#1e293b"
        border = "#3b82f6" if active else "#334155"
        params = f"verdict={v}&sort={sort}" if v else f"sort={sort}"
        return (f'<a href="/?{params}" style="background:{bg};border:1px solid {border};'
                f'color:#e2e8f0;padding:4px 12px;border-radius:6px;font-size:0.78rem;'
                f'text-decoration:none;white-space:nowrap">{label}</a>')

    sort_label = "↑ Oldest First" if ascending else "↓ Newest First"
    sort_toggle = "asc" if not ascending else "desc"
    sort_href = f"/?verdict={verdict}&sort={sort_toggle}"

    gk_rows = "".join(
        f"<tr>"
        f"<td style='white-space:nowrap'>{e['ts']}</td>"
        f"<td><a href='https://gitlab.com/alexvas10-group/sandbox-repoguard/-/merge_requests/{e['mr_iid']}' "
        f"style='color:#7dd3fc;text-decoration:none' target='_blank'>!{e['mr_iid']}</a></td>"
        f"<td style='max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{e.get('mr_title','')}</td>"
        f"<td>{verdict_badge(e['verdict'])}</td>"
        f"</tr>"
        for e in gk_events
    ) or "<tr><td colspan='4' style='text-align:center;color:#475569;padding:2rem'>No events yet</td></tr>"

    gd_rows = "".join(
        f"<tr>"
        f"<td style='white-space:nowrap'>{e['ts']}</td>"
        f"<td><code style='color:#f472b6'>{e.get('commit_sha','')}</code></td>"
        f"<td>{e.get('error_type','')}</td>"
        f"<td><a href='https://gitlab.com/alexvas10-group/sandbox-repoguard/-/merge_requests/{e['mr_iid']}' "
        f"style='color:#7dd3fc;text-decoration:none' target='_blank'>!{e['mr_iid']}</a></td>"
        f"<td>{status_badge(e.get('status',''))}</td>"
        f"</tr>"
        for e in gd_events
    ) or "<tr><td colspan='5' style='text-align:center;color:#475569;padding:2rem'>No events yet</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RepoGuard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
  header{{padding:1.75rem 2rem;border-bottom:1px solid #1e293b;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:1rem}}
  .logo{{font-size:1.4rem;font-weight:700;letter-spacing:-0.5px}}
  .sub{{color:#64748b;font-size:0.85rem;margin-top:4px}}
  .live{{display:inline-flex;align-items:center;gap:6px;font-size:0.8rem;color:#4ade80}}
  .dot{{width:8px;height:8px;border-radius:50%;background:#4ade80;animation:pulse 2s infinite}}
  @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
  .toolbar{{padding:1rem 2rem;display:flex;align-items:center;gap:0.75rem;flex-wrap:wrap;border-bottom:1px solid #1e293b}}
  .toolbar-label{{font-size:0.78rem;color:#64748b;margin-right:4px}}
  .sort-btn{{background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:4px 12px;border-radius:6px;font-size:0.78rem;text-decoration:none;white-space:nowrap}}
  .sort-btn:hover{{border-color:#64748b}}
  .divider{{color:#334155;font-size:0.9rem}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;padding:1.5rem 2rem}}
  @media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}
  .card{{background:#1e293b;border-radius:10px;padding:1.5rem;border:1px solid #334155}}
  .card-title{{font-size:1rem;font-weight:600;margin-bottom:1.25rem}}
  table{{width:100%;border-collapse:collapse;font-size:0.83rem}}
  th{{text-align:left;color:#64748b;font-size:0.7rem;text-transform:uppercase;letter-spacing:.05em;padding:0 0.5rem 0.6rem;border-bottom:1px solid #334155}}
  td{{padding:0.65rem 0.5rem;border-bottom:1px solid #0f172a;vertical-align:middle}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#243044}}
  .footer{{text-align:center;padding:1.5rem;color:#334155;font-size:0.75rem}}
</style>
</head>
<body>
<header>
  <div>
    <div class="logo">🛡️ RepoGuard</div>
    <div class="sub">Autonomous GitLab Code Guardian · Gemini 2.5 Flash · Google Cloud Run</div>
  </div>
  <div class="live"><span class="dot"></span>Live</div>
</header>
<div class="toolbar">
  <span class="toolbar-label">Filter by verdict:</span>
  {filter_link("All", "")}
  {filter_link("✅ Approved", "APPROVED")}
  {filter_link("🔴 Rejected", "REJECTED")}
  {filter_link("🟡 Needs Review", "NEEDS_REVIEW")}
  <span class="divider">|</span>
  <a href="{sort_href}" class="sort-btn">{sort_label}</a>
</div>
<div class="grid">
  <div class="card">
    <div class="card-title">🛡️ Gatekeeper — MR Verdicts</div>
    <table>
      <thead><tr><th>Time</th><th>MR</th><th>Title</th><th>Verdict</th></tr></thead>
      <tbody>{gk_rows}</tbody>
    </table>
  </div>
  <div class="card">
    <div class="card-title">🚨 Guardian — Incident Response</div>
    <table>
      <thead><tr><th>Time</th><th>Commit</th><th>Error</th><th>MR</th><th>Status</th></tr></thead>
      <tbody>{gd_rows}</tbody>
    </table>
  </div>
</div>
<div class="footer">Built for the Google Cloud Rapid Agent Hackathon · GitLab Partner Track</div>
</body>
</html>"""
    return HTMLResponse(content=html)

