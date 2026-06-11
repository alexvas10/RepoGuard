import asyncio
import base64
import json
import logging
from typing import Optional, Any
import httpx
from core.config import settings

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0
_TRANSIENT = (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError)

async def _retry(coro_fn, *args, attempts: int = 3, **kwargs):
    for attempt in range(attempts):
        try:
            return await coro_fn(*args, **kwargs)
        except _TRANSIENT as exc:
            if attempt == attempts - 1:
                raise
            wait = 2 ** attempt
            logger.warning("[tools] transient error (attempt %d/%d), retrying in %ds: %s", attempt + 1, attempts, wait, exc)
            await asyncio.sleep(wait)

def _get_headers():
    return {
        "Authorization": f"Bearer {settings.GITLAB_PAT}",
        "Content-Type": "application/json",
    }

# ---------------------------------------------------------------------------
# GitLab REST API Tools
# ---------------------------------------------------------------------------

async def get_file_tool(project_id: int, file_path: str, ref: str = "main") -> str:
    """
    Retrieves the content of a file from a GitLab repository.
    - project_id: The ID of the GitLab project.
    - file_path: Path to the file (e.g., '.repoguard/scope.json').
    - ref: The branch, tag or commit SHA.
    """
    encoded_path = file_path.replace("/", "%2F")
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/repository/files/{encoded_path}"
    async def _fetch():
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            return await client.get(url, headers=_get_headers(), params={"ref": ref})
    resp = await _retry(_fetch)
    if resp.status_code == 404:
        return f"Error: File {file_path} not found."
    resp.raise_for_status()
    content = base64.b64decode(resp.json()["content"]).decode("utf-8")
    return content

async def get_mr_changes_tool(project_id: int, mr_iid: int) -> str:
    """
    Retrieves the diff/changes for a GitLab Merge Request.
    - project_id: The ID of the GitLab project.
    - mr_iid: The IID of the merge request.
    """
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/merge_requests/{mr_iid}/changes"
    async def _fetch():
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            return await client.get(url, headers=_get_headers())
    resp = await _retry(_fetch)
    resp.raise_for_status()
    changes = resp.json().get("changes", [])
    
    lines = []
    for change in changes:
        lines.append(f"--- {change.get('old_path', '')}")
        lines.append(f"+++ {change.get('new_path', '')}")
        lines.append(change.get("diff", ""))
    full = "\n".join(lines)
    if len(full) > 8000:
        return full[:8000] + "\n... [diff truncated]"
    return full

async def get_mr_details_tool(project_id: int, mr_iid: int) -> str:
    """
    Retrieves the metadata for a GitLab Merge Request (title, author, labels).
    """
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/merge_requests/{mr_iid}"
    async def _fetch():
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            return await client.get(url, headers=_get_headers())
    resp = await _retry(_fetch)
    resp.raise_for_status()
    return json.dumps(resp.json())

async def update_mr_tool(project_id: int, mr_iid: int, state_event: Optional[str] = None, add_labels: Optional[str] = None, draft: Optional[bool] = None) -> str:
    """
    Updates a GitLab Merge Request (e.g., closing it, adding labels, or setting draft status).
    - state_event: Set to 'close' or 'reopen' to change state.
    - add_labels: Comma-separated list of labels to add.
    """
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/merge_requests/{mr_iid}"
    payload = {}
    if state_event: payload["state_event"] = state_event
    if add_labels: payload["add_labels"] = add_labels
    if draft is not None: payload["draft"] = draft
    
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.put(url, headers=_get_headers(), json=payload)
    resp.raise_for_status()
    return f"Successfully updated MR !{mr_iid}"

async def create_branch_tool(project_id: int, branch_name: str, ref: str = "main") -> str:
    """Creates a new branch in the repository."""
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/repository/branches"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers=_get_headers(), json={"branch": branch_name, "ref": ref})
    resp.raise_for_status()
    return f"Successfully created branch {branch_name}"

async def create_mr_tool(project_id: int, source_branch: str, title: str, description: str) -> str:
    """Creates a new Merge Request."""
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/merge_requests"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers=_get_headers(), json={
            "source_branch": source_branch,
            "target_branch": "main",
            "title": title,
            "description": description,
            "labels": "repoguard::auto-remediation",
            "draft": True,
        })
    resp.raise_for_status()
    return json.dumps(resp.json())

async def revert_commit_tool(project_id: int, commit_sha: str, branch: str) -> str:
    """Reverts a specific commit on the target branch."""
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/repository/commits/{commit_sha}/revert"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers=_get_headers(), json={"branch": branch})
    if resp.status_code in (400, 409):
        return f"Error: Revert failed due to conflict: {resp.text}"
    resp.raise_for_status()
    return f"Successfully reverted commit {commit_sha}"

async def get_commits_in_window_tool(project_id: int, since: str, until: str) -> str:
    """Retrieves commits between two timestamps (ISO 8601)."""
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/repository/commits"
    params = {"since": since, "until": until, "per_page": 20, "ref_name": "main"}
    async def _fetch():
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            return await client.get(url, headers=_get_headers(), params=params)
    resp = await _retry(_fetch)
    resp.raise_for_status()
    return json.dumps(resp.json())

async def get_commit_diff_tool(project_id: int, commit_sha: str) -> str:
    """Retrieves the diff for a specific commit."""
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/repository/commits/{commit_sha}/diff"
    async def _fetch():
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            return await client.get(url, headers=_get_headers())
    resp = await _retry(_fetch)
    resp.raise_for_status()
    diff_data = resp.json()
    diff_text = "\n".join(
        f"--- {d.get('old_path', '')}\n+++ {d.get('new_path', '')}\n{d.get('diff', '')}"
        for d in diff_data[:5]
    )
    if len(diff_text) > 6000:
        return diff_text[:6000] + "\n... [truncated]"
    return diff_text

async def post_mr_comment_tool(project_id: int, merge_request_iid: int, body: str) -> str:
    """Posts a comment (note) on a GitLab Merge Request."""
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/merge_requests/{merge_request_iid}/notes"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers=_get_headers(), json={"body": body})
    resp.raise_for_status()
    return f"Successfully posted comment on MR !{merge_request_iid}"

async def get_wiki_page_tool(project_id: int, slug: str) -> str:
    """
    Retrieves the content of a GitLab Wiki page.
    - project_id: The ID of the GitLab project.
    - slug: The slug (URL-friendly name) of the wiki page (e.g., 'home', 'architectural-rules').
    """
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/wikis/{slug}"
    async def _fetch():
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            return await client.get(url, headers=_get_headers())
    resp = await _retry(_fetch)
    if resp.status_code == 404:
        return f"Error: Wiki page '{slug}' not found."
    resp.raise_for_status()
    return resp.json().get("content", "Error: No content found in wiki page.")

async def create_project_tool(name: str, namespace_id: Optional[int] = None, description: str = "") -> str:
    """
    Creates a new GitLab project.
    - name: The name of the new project.
    - namespace_id: The ID of the group or user namespace to create the project in.
    - description: A short description of the project.
    """
    url = f"{settings.GITLAB_API_URL}/projects"
    payload = {"name": name, "description": description, "initialize_with_readme": True}
    if namespace_id:
        payload["namespace_id"] = namespace_id
    
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers=_get_headers(), json=payload)
    resp.raise_for_status()
    project = resp.json()
    return json.dumps({"project_id": project["id"], "web_url": project["web_url"]})

async def list_groups_tool(search: Optional[str] = None) -> str:
    """Lists the groups the current user has access to."""
    url = f"{settings.GITLAB_API_URL}/groups"
    params = {"min_access_level": 30} # Developer or higher
    if search:
        params["search"] = search
    
    async def _fetch():
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            return await client.get(url, headers=_get_headers(), params=params)
    resp = await _retry(_fetch)
    resp.raise_for_status()
    groups = [{"id": g["id"], "full_path": g["full_path"]} for g in resp.json()]
    return json.dumps(groups)

# ---------------------------------------------------------------------------
# Event Logging Tools
# ---------------------------------------------------------------------------

async def log_gatekeeper_event_tool(mr_iid: int, project_id: int, verdict: str, mr_title: str) -> str:
    """Logs a Gatekeeper analysis event to Firestore."""
    from core.events import log_gatekeeper
    await log_gatekeeper(mr_iid, project_id, verdict, mr_title)
    return "Event logged successfully."

async def log_guardian_event_tool(commit_sha: str, error_type: str, service: str, mr_iid: int, status: str) -> str:
    """Logs a Guardian remediation event to Firestore."""
    from core.events import log_guardian
    await log_guardian(commit_sha, error_type, service, mr_iid, status)
    return "Event logged successfully."

async def update_guardian_status_tool(mr_iid: int, status: str) -> str:
    """Updates the status of a Guardian event in Firestore."""
    from core.events import update_guardian_status
    await update_guardian_status(mr_iid, status)
    return "Status updated successfully."

async def create_multiple_files_tool(project_id: int, branch: str, commit_message: str, actions: list[dict[str, str]]) -> str:
    """
    Performs a bulk commit with multiple actions (create, update, delete, etc.).
    - actions: A list of dicts, each with 'action' (create, delete, move, update, chmod), 'file_path', and 'content'.
    """
    url = f"{settings.GITLAB_API_URL}/projects/{project_id}/repository/commits"
    payload = {
        "branch": branch,
        "commit_message": commit_message,
        "actions": actions
    }
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers=_get_headers(), json=payload)
    resp.raise_for_status()
    return f"Successfully committed {len(actions)} actions to branch {branch}"
