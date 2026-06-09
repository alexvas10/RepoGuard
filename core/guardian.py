import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .gitlab_client import GitLabClient
from .agent_client import invoke_agent, GUARDIAN_TOOLS
from .models import AlertPayload, PendingRollback
from .events import log_guardian, update_guardian_status

logger = logging.getLogger(__name__)

# In-memory store: token -> PendingRollback
pending_rollbacks: dict[str, PendingRollback] = {}

FORENSIC_PROMPT = """
You are RepoGuard Guardian — an incident response analyst.

A production alert has fired. Review the commit diff against the stack trace and answer in one sentence: does this commit plausibly explain the error? Be direct.

ERROR TYPE: {error_type}
SERVICE: {service}

STACK TRACE:
{stack_trace}

CANDIDATE COMMIT:
SHA: {commit_sha}
Author: {commit_author}
Message: {commit_message}
Timestamp: {commit_timestamp}

COMMIT DIFF:
{commit_diff}

Respond with one sentence only.
"""

COMMENT_PROMPT = """
You are RepoGuard Guardian. A rollback MR has been created for a production incident.

Call create_merge_request_note with project_id={project_id}, merge_request_iid={mr_iid} and exactly this body:

## 🚨 RepoGuard Auto-Remediation

| | |
|:--|:--|
| **Alert received** | `{alert_timestamp}` |
| **Error** | `{error_type}` in `{service}` |
| **Root cause commit** | `{commit_sha}` |
| **Commit message** | {commit_message} |

**Forensic Analysis**
> {forensic_analysis}

<details>
<summary>📋 Stack Trace</summary>

```
{stack_trace}
```

</details>

---
> ⚠️ This MR was created automatically by RepoGuard. **A human must review and merge.**

**👉 [Approve this rollback]({approve_url})**

*Powered by RepoGuard · Gemini 2.5 Flash*

Take no other actions.
"""


def _parse_stack_trace_files(stack_trace: str) -> list[str]:
    files = []
    for line in stack_trace.splitlines():
        if "File '" in line or 'File "' in line:
            try:
                path = line.split("File ")[1].split("'")[1] if "'" in line else line.split('"')[1]
                files.append(path.lstrip("/").lstrip("./"))
            except IndexError:
                pass
    return files


def _pick_candidate(commits: list[dict], stack_files: list[str]) -> Optional[dict]:
    if not commits:
        return None
    for commit in commits:
        modified = commit.get("modified_paths", []) or []
        if any(f in path for f in stack_files for path in modified):
            return commit
    return commits[0]


async def process_alert(project_id: int, payload: AlertPayload, commit_sha: str | None = None, base_url: str = "https://repoguard-926140091197.us-central1.run.app") -> str:
    gitlab = GitLabClient()
    await gitlab.ensure_labels(project_id)

    if commit_sha:
        candidate = await gitlab.get_commit(project_id, commit_sha)
    else:
        alert_dt = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
        window_start = (alert_dt - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        window_end = alert_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        commits = await gitlab.get_commits_in_window(project_id, window_start, window_end)
        if not commits:
            logger.warning("No commits found in window for alert at %s", payload.timestamp)
            return "no commits found in window"
        stack_files = _parse_stack_trace_files(payload.stack_trace)
        candidate = _pick_candidate(commits, stack_files)

    commit_sha = candidate["id"]
    commit_sha_short = commit_sha[:8]

    diff_data = await gitlab.get_commit_diff(project_id, commit_sha)
    diff_text = "\n".join(
        f"--- {d.get('old_path', '')}\n+++ {d.get('new_path', '')}\n{d.get('diff', '')}"
        for d in diff_data[:5]
    )
    if len(diff_text) > 6000:
        diff_text = diff_text[:6000] + "\n... [truncated]"

    # Phase 1 — Gemini confirms causation (text only, no tools)
    forensic_prompt = FORENSIC_PROMPT.format(
        error_type=payload.error_type,
        service=payload.service,
        stack_trace=payload.stack_trace,
        commit_sha=commit_sha,
        commit_author=candidate.get("author_name", "unknown"),
        commit_message=candidate.get("title", ""),
        commit_timestamp=candidate.get("created_at", ""),
        commit_diff=diff_text,
    )
    logger.info("Requesting forensic analysis for commit %s", commit_sha_short)
    forensic_analysis = await invoke_agent(forensic_prompt)
    logger.info("Forensic analysis: %s", forensic_analysis)

    # Phase 2 — Core Engine creates rollback branch + MR via REST (need the MR IID)
    rollback_branch = f"emergency/rollback-{commit_sha_short}"
    logger.info("Creating rollback branch %s", rollback_branch)
    await gitlab.create_branch(project_id, rollback_branch, ref=f"{commit_sha}~1")

    revert_result = await gitlab.revert_commit(project_id, commit_sha, rollback_branch)
    if "error" in revert_result:
        logger.warning("revert_commit conflict (%s) — branch points to parent commit", revert_result["error"])

    mr_title = f"[AUTO-REMEDIATION] Rollback {commit_sha_short} — {payload.error_type}"
    mr_description = (
        f"## 🚨 RepoGuard Auto-Remediation\n\n"
        f"| | |\n"
        f"|:--|:--|\n"
        f"| **Alert received** | `{payload.timestamp}` |\n"
        f"| **Error** | `{payload.error_type}` in `{payload.service}` |\n"
        f"| **Root cause commit** | `{commit_sha}` |\n\n"
        f"**Forensic Analysis**\n"
        f"> {forensic_analysis}\n\n"
        f"<details>\n<summary>📋 Stack Trace</summary>\n\n"
        f"```\n{payload.stack_trace}\n```\n\n</details>\n\n"
        f"---\n"
        f"> ⚠️ This MR was created automatically by RepoGuard. **A human must review and merge.**\n\n"
        f"*Powered by RepoGuard · Gemini 2.5 Flash*"
    )
    mr_data = await gitlab.create_mr(project_id, rollback_branch, mr_title, mr_description)
    mr_iid = mr_data["iid"]
    logger.info("Rollback MR !%s created", mr_iid)

    await log_guardian(commit_sha, payload.error_type, payload.service, mr_iid, "pending approval")

    token = str(uuid.uuid4())
    pending_rollbacks[token] = PendingRollback(
        token=token,
        project_id=project_id,
        mr_iid=mr_iid,
        commit_sha=commit_sha,
        created_at=datetime.now(timezone.utc),
    )
    logger.info("Rollback pending approval — token: %s, MR: !%s", token, mr_iid)

    approve_url = f"{base_url}/rollback/confirm/{token}/{mr_iid}"

    # Phase 3 — Gemini posts structured forensic comment via MCP tool
    comment_prompt = COMMENT_PROMPT.format(
        project_id=project_id,
        mr_iid=mr_iid,
        alert_timestamp=payload.timestamp,
        error_type=payload.error_type,
        service=payload.service,
        commit_sha=commit_sha,
        commit_message=candidate.get("title", ""),
        forensic_analysis=forensic_analysis,
        stack_trace=payload.stack_trace,
        approve_url=approve_url,
    )
    logger.info("Invoking Guardian agent to post comment on MR !%s", mr_iid)
    await invoke_agent(comment_prompt, tools=GUARDIAN_TOOLS)

    return token, mr_iid


async def approve_rollback(project_id: int, mr_iid: int, token: str) -> str:
    rollback = pending_rollbacks.get(token)
    if not rollback:
        return "invalid or expired token"
    if rollback.mr_iid != mr_iid or rollback.project_id != project_id:
        return "token does not match the specified MR"

    gitlab = GitLabClient()
    await gitlab.update_mr(
        project_id,
        mr_iid,
        draft=False,
        add_labels="repoguard::approved-rollback",
    )
    del pending_rollbacks[token]
    await update_guardian_status(mr_iid, "approved — ready to merge")
    logger.info("Rollback MR !%s approved and marked ready to merge", mr_iid)
    return f"MR !{mr_iid} is now ready to merge"
