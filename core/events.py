import logging
from google.cloud import firestore
from .config import settings

logger = logging.getLogger(__name__)

_db: firestore.AsyncClient | None = None


def _get_db() -> firestore.AsyncClient:
    global _db
    if _db is None:
        _db = firestore.AsyncClient(project=settings.GCP_PROJECT_ID)
    return _db


async def log_gatekeeper(mr_iid: int, project_id: int, verdict: str, mr_title: str) -> None:
    try:
        await _get_db().collection("gatekeeper_events").add({
            "ts": firestore.SERVER_TIMESTAMP,
            "mr_iid": mr_iid,
            "project_id": project_id,
            "verdict": verdict,
            "mr_title": mr_title,
        })
    except Exception as exc:
        logger.warning("Firestore write failed (gatekeeper): %s", exc)


async def log_guardian(commit_sha: str, error_type: str, service: str, mr_iid: int, status: str) -> None:
    try:
        await _get_db().collection("guardian_events").add({
            "ts": firestore.SERVER_TIMESTAMP,
            "commit_sha": commit_sha[:8],
            "error_type": error_type,
            "service": service,
            "mr_iid": mr_iid,
            "status": status,
        })
    except Exception as exc:
        logger.warning("Firestore write failed (guardian): %s", exc)


async def update_guardian_status(mr_iid: int, status: str) -> None:
    try:
        db = _get_db()
        docs = await db.collection("guardian_events").where("mr_iid", "==", mr_iid).get()
        for doc in docs:
            await doc.reference.update({"status": status})
    except Exception as exc:
        logger.warning("Firestore update failed (guardian status): %s", exc)


def _fmt(ts) -> str:
    try:
        return ts.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "—"


async def get_gatekeeper_events(verdict_filter: str | None = None, ascending: bool = False) -> list[dict]:
    try:
        direction = "ASCENDING" if ascending else "DESCENDING"
        docs = await _get_db().collection("gatekeeper_events").order_by("ts", direction=direction).limit(200).get()
        events = [{"ts": _fmt(d.to_dict().get("ts")), **{k: v for k, v in d.to_dict().items() if k != "ts"}} for d in docs]
        if verdict_filter:
            events = [e for e in events if e.get("verdict", "").upper() == verdict_filter.upper()]
        return events
    except Exception as exc:
        logger.warning("Firestore read failed (gatekeeper): %s", exc)
        return []


async def get_guardian_events(ascending: bool = False) -> list[dict]:
    try:
        direction = "ASCENDING" if ascending else "DESCENDING"
        docs = await _get_db().collection("guardian_events").order_by("ts", direction=direction).limit(200).get()
        return [{"ts": _fmt(d.to_dict().get("ts")), **{k: v for k, v in d.to_dict().items() if k != "ts"}} for d in docs]
    except Exception as exc:
        logger.warning("Firestore read failed (guardian): %s", exc)
        return []
