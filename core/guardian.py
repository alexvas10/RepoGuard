import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .gitlab_client import GitLabClient
from .agent_client import invoke_agent
from .models import AlertPayload, PendingRollback

logger = logging.getLogger(__name__)

# In-memory store: token -> PendingRollback
pending_rollbacks: dict[str, PendingRollback] = {}

GUARDIAN_PROMPT = """
You are RepoGuard Guardian — an autonomous incident response agent for a GitLab repository.

A production alert has fired. You have been given forensic analysis results below. Your job is to create an emergency rollback Merge Request using your GitLab tools.

---
PROJECT ID: {project_id}
ALERT TIMESTAMP: {alert_timestamp}
ERROR TYPE: {error_type}
SERVICE: {service}
SEVERITY: {severity}

STACK TRACE:
{stack_trace}

CANDIDATE COMMIT (most likely cause):
SHA: {commit_sha}
Author: {commit_author}
Message: {commit_message}
Timestamp: {commit_timestamp}

COMMIT DIFF:
{commit_diff}

ROLLBACK BRANCH: {rollback_branch}
---

INSTRUCTIONS — follow these steps in order:

Step 1: Review the commit diff against the stack trace. Confirm in one sentence whether this commit plausibly caused the error.

Step 2: The rollback branch "{rollback_branch}" has already been created. Call create_commit or revert_commit to revert commit {commit_sha} on branch "{rollback_branch}" in project {project_id}.

Step 3: Call create_merge_request on project_id={project_id} with:
  - source_branch: {rollback_branch}
  - target_branch: main
  - title: "[AUTO-REMEDIATION] Rollback {commit_sha_short} — {error_type}"
  - description:
    "## RepoGuard Auto-Remediation\\n\\n**Triggered by:** Production alert at {alert_timestamp}\\n**Error:** {error_type} in {service}\\n**Root cause commit:** {commit_sha}\\n**Commit message:** {commit_message}\\n\\n> This MR was created automatically. A human must approve and merge.\\n\\n**Stack trace:**\\n```\\n{stack_trace}\\n```"
  - draft: true

Take no other actions. Do not merge the MR.
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


def _score_commits(commits: list[dict], stack_files: list[str]) -> Optional[dict]:
    if not commits:
        return None

    for commit in commits:
        for file in stack_files:
            if file in str(commit.get("id", "")):
                return commit

    return commits[0]


async def process_alert(project_id: int, payload: AlertPayload) -> str:
    gitlab = GitLabClient()

    alert_dt = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
    window_start = (alert_dt - timedelta(minutes=10)).isoformat()
    window_end = alert_dt.isoformat()

    commits = await gitlab.get_commits_in_window(project_id, window_start, window_end)
    if not commits:
        logger.warning("No commits found in window for alert at %s", payload.timestamp)
        return "no commits found in window"

    stack_files = _parse_stack_trace_files(payload.stack_trace)
    candidate = _score_commits(commits, stack_files)

    commit_sha = candidate["id"]
    commit_sha_short = commit_sha[:8]
    rollback_branch = f"emergency/rollback-{commit_sha_short}"

    logger.info("Creating rollback branch %s", rollback_branch)
    await gitlab.create_branch(project_id, rollback_branch, ref=commit_sha + "~1")

    diff_data = await gitlab.get_commit_diff(project_id, commit_sha)
    diff_text = "\n".join(
        f"--- {d.get('old_path', '')}\n+++ {d.get('new_path', '')}\n{d.get('diff', '')}"
        for d in diff_data[:5]
    )
    if len(diff_text) > 6000:
        diff_text = diff_text[:6000] + "\n... [truncated]"

    prompt = GUARDIAN_PROMPT.format(
        project_id=project_id,
        alert_timestamp=payload.timestamp,
        error_type=payload.error_type,
        service=payload.service,
        severity=payload.severity,
        stack_trace=payload.stack_trace,
        commit_sha=commit_sha,
        commit_sha_short=commit_sha_short,
        commit_author=candidate.get("author_name", "unknown"),
        commit_message=candidate.get("title", ""),
        commit_timestamp=candidate.get("created_at", ""),
        commit_diff=diff_text,
        rollback_branch=rollback_branch,
    )

    logger.info("Invoking Guardian agent for commit %s", commit_sha_short)
    result = await invoke_agent(prompt)
    logger.info("Agent response: %s", result)

    # Parse MR IID from agent response so we can store the rollback token
    mr_iid = _extract_mr_iid(result)
    if mr_iid:
        token = str(uuid.uuid4())
        pending_rollbacks[token] = PendingRollback(
            token=token,
            project_id=project_id,
            mr_iid=mr_iid,
            commit_sha=commit_sha,
            created_at=datetime.now(timezone.utc),
        )
        logger.info("Rollback pending approval — token: %s, MR: !%s", token, mr_iid)
        return f"rollback MR !{mr_iid} created. Approval token: {token}"

    return result


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
        labels="repoguard::approved-rollback",
    )
    del pending_rollbacks[token]
    logger.info("Rollback MR !%s approved and marked ready to merge", mr_iid)
    return f"MR !{mr_iid} is now ready to merge"


def _extract_mr_iid(text: str) -> Optional[int]:
    import re
    match = re.search(r"[!/](\d+)", text)
    if match:
        return int(match.group(1))
    return None
