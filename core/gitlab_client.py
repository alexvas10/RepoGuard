import httpx
import json
from typing import Optional
from .config import settings


class GitLabClient:
    def __init__(self):
        self.base_url = settings.GITLAB_API_URL
        self.headers = {
            "Authorization": f"Bearer {settings.GITLAB_PAT}",
            "Content-Type": "application/json",
        }

    async def get_file(self, project_id: int, file_path: str, ref: str = "main") -> Optional[str]:
        encoded_path = file_path.replace("/", "%2F")
        url = f"{self.base_url}/projects/{project_id}/repository/files/{encoded_path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers, params={"ref": ref})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        import base64
        return base64.b64decode(resp.json()["content"]).decode("utf-8")

    async def get_mr_changes(self, project_id: int, mr_iid: int) -> dict:
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{mr_iid}/changes"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def get_mr(self, project_id: int, mr_iid: int) -> dict:
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{mr_iid}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def get_commits_in_window(self, project_id: int, since: str, until: str) -> list[dict]:
        url = f"{self.base_url}/projects/{project_id}/repository/commits"
        params = {"since": since, "until": until, "per_page": 20}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_commit_diff(self, project_id: int, commit_sha: str) -> list[dict]:
        url = f"{self.base_url}/projects/{project_id}/repository/commits/{commit_sha}/diff"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def create_branch(self, project_id: int, branch_name: str, ref: str = "main") -> dict:
        url = f"{self.base_url}/projects/{project_id}/repository/branches"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers=self.headers,
                json={"branch": branch_name, "ref": ref},
            )
        resp.raise_for_status()
        return resp.json()

    async def create_mr(self, project_id: int, source_branch: str, title: str, description: str) -> dict:
        url = f"{self.base_url}/projects/{project_id}/merge_requests"
        async with httpx.AsyncClient() as client:
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
        async with httpx.AsyncClient() as client:
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
        async with httpx.AsyncClient() as client:
            resp = await client.put(url, headers=self.headers, json=kwargs)
        resp.raise_for_status()
        return resp.json()

    async def post_mr_comment(self, project_id: int, mr_iid: int, body: str) -> dict:
        url = f"{self.base_url}/projects/{project_id}/merge_requests/{mr_iid}/notes"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self.headers, json={"body": body})
        resp.raise_for_status()
        return resp.json()

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
