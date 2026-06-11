import asyncio
import httpx
import json
import logging
from typing import Optional
from .config import settings

logger = logging.getLogger(__name__)

_labels_initialized: set[int] = set()
_HTTP_TIMEOUT = 60.0
_TRANSIENT = (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException)


async def _retry(coro_fn, *args, attempts: int = 5, **kwargs):
    """Call an async function with exponential backoff on transient httpx errors."""
    for attempt in range(attempts):
        try:
            return await coro_fn(*args, **kwargs)
        except _TRANSIENT as exc:
            if attempt == attempts - 1:
                raise
            wait = 2 ** attempt
            logger.warning("[gitlab] transient error (attempt %d/%d), retrying in %ds: %s", attempt + 1, attempts, wait, exc)
            await asyncio.sleep(wait)


class GitLabClient:
    def __init__(self):
        self.base_url = settings.GITLAB_API_URL
        self.headers = {
            "Authorization": f"Bearer {settings.GITLAB_PAT}",
            "Content-Type": "application/json",
        }

    async def get_file(self, project_id: int, file_path: str, ref: str = "main") -> Optional[str]:
        import base64
        encoded_path = file_path.replace("/", "%2F")
        url = f"{self.base_url}/projects/{project_id}/repository/files/{encoded_path}"
        async def _fetch():
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                return await client.get(url, headers=self.headers, params={"ref": ref})
        resp = await _retry(_fetch)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return base64.b64decode(resp.json()["content"]).decode("utf-8")

    async def get_mr_changes(self, project_id: int, mr_iid: int) -> dict:
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{mr_iid}/changes"
        async def _fetch():
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                return await client.get(url, headers=self.headers)
        resp = await _retry(_fetch)
        resp.raise_for_status()
        return resp.json()

    async def get_mr(self, project_id: int, mr_iid: int) -> dict:
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{mr_iid}"
        async def _fetch():
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                return await client.get(url, headers=self.headers)
        resp = await _retry(_fetch)
        resp.raise_for_status()
        return resp.json()

    async def get_commits_in_window(self, project_id: int, since: str, until: str) -> list[dict]:
        url = f"{self.base_url}/projects/{project_id}/repository/commits"
        params = {"since": since, "until": until, "per_page": 20, "ref_name": "main"}
        async def _fetch():
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                return await client.get(url, headers=self.headers, params=params)
        resp = await _retry(_fetch)
        resp.raise_for_status()
        return resp.json()

    async def get_commit(self, project_id: int, commit_sha: str) -> dict:
        url = f"{self.base_url}/projects/{project_id}/repository/commits/{commit_sha}"
        async def _fetch():
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                return await client.get(url, headers=self.headers)
        resp = await _retry(_fetch)
        resp.raise_for_status()
        return resp.json()

    async def get_commit_diff(self, project_id: int, commit_sha: str) -> list[dict]:
        url = f"{self.base_url}/projects/{project_id}/repository/commits/{commit_sha}/diff"
        async def _fetch():
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                return await client.get(url, headers=self.headers)
        resp = await _retry(_fetch)
        resp.raise_for_status()
        return resp.json()

    async def create_branch(self, project_id: int, branch_name: str, ref: str = "main") -> dict:
        url = f"{self.base_url}/projects/{project_id}/repository/branches"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers=self.headers,
                json={"branch": branch_name, "ref": ref},
            )
        resp.raise_for_status()
        return resp.json()

    async def create_mr(self, project_id: int, source_branch: str, title: str, description: str) -> dict:
        url = f"{self.base_url}/projects/{project_id}/merge_requests"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers=self.headers,
                json={
                    "source_branch": source_branch,
                    "target_branch": "main",
                    "title": title,
                    "description": description,
                    "labels": "repoguard::auto-remediation",
                    "draft": True,
                },
            )
        resp.raise_for_status()
        return resp.json()

    async def revert_commit(self, project_id: int, commit_sha: str, branch: str) -> dict:
        url = f"{self.base_url}/projects/{project_id}/repository/commits/{commit_sha}/revert"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers=self.headers,
                json={"branch": branch},
            )
        if resp.status_code in (400, 409):
            return {"error": resp.json()}
        resp.raise_for_status()
        return resp.json()

    async def update_mr(self, project_id: int, mr_iid: int, **kwargs) -> dict:
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{mr_iid}"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.put(url, headers=self.headers, json=kwargs)
        resp.raise_for_status()
        return resp.json()

    async def post_mr_comment(self, project_id: int, mr_iid: int, body: str) -> dict:
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{mr_iid}/notes"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, headers=self.headers, json={"body": body})
        resp.raise_for_status()
        return resp.json()

    async def ensure_labels(self, project_id: int) -> None:
        if project_id in _labels_initialized:
            return
        labels = [
            {"name": "repoguard::approved", "color": "#28a745", "description": "RepoGuard: MR approved"},
            {"name": "repoguard::rejected", "color": "#dc3545", "description": "RepoGuard: MR rejected"},
            {"name": "repoguard::needs-review", "color": "#fd7e14", "description": "RepoGuard: human review needed"},
            {"name": "repoguard::auto-remediation", "color": "#6f42c1", "description": "RepoGuard: auto-remediation MR"},
            {"name": "repoguard::approved-rollback", "color": "#17a2b8", "description": "RepoGuard: rollback approved"},
        ]
        url = f"{self.base_url}/projects/{project_id}/labels"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            for label in labels:
                resp = await client.post(url, headers=self.headers, json=label)
                if resp.status_code not in (201, 409):
                    logger.warning("Failed to create label %s: %s", label["name"], resp.status_code)
        _labels_initialized.add(project_id)

    def format_diff(self, changes: list[dict], max_chars: int = 8000) -> str:
        lines = []
        for change in changes:
            lines.append(f"--- {change.get('old_path', '')}")
            lines.append(f"+++ {change.get('new_path', '')}")
            lines.append(change.get("diff", ""))
        full = "\n".join(lines)
        if len(full) > max_chars:
            return full[:max_chars] + "\n... [diff truncated]"
        return full
