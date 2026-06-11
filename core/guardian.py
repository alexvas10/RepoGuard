import logging
from datetime import datetime, timezone
from .models import PendingRollback
from .events import update_guardian_status
from .config import settings
import httpx

logger = logging.getLogger(__name__)

# In-memory store: token -> PendingRollback
pending_rollbacks: dict[str, PendingRollback] = {}

async def approve_rollback(project_id: int, mr_iid: int, token: str) -> str:
    rollback = pending_rollbacks.get(token)
    if not rollback:
        return "invalid or expired token"
    if rollback.mr_iid != mr_iid or rollback.project_id != project_id:
        return "token does not match the specified MR"

    # Use a simple REST call to update the MR
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/merge_requests/{mr_iid}"
    headers = {
        "Authorization": f"Bearer {settings.GITLAB_PAT}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.put(url, headers=headers, json={
            "draft": False,
            "add_labels": "repoguard::approved-rollback",
        })
    resp.raise_for_status()

    del pending_rollbacks[token]
    await update_guardian_status(mr_iid, "approved — ready to merge")
    logger.info("Rollback MR !%s approved and marked ready to merge", mr_iid)
    return f"MR !{mr_iid} is now ready to merge"
