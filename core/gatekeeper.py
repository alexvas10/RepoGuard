import asyncio
import re
import logging
from .gitlab_client import GitLabClient
from .agent_client import invoke_agent, GATEKEEPER_TOOLS
from .events import log_gatekeeper

logger = logging.getLogger(__name__)

# Only one Gatekeeper AI call runs at a time.  Pre-fetching GitLab data happens
# BEFORE acquiring this semaphore, so two MRs' API calls overlap with each other
# while one AI call is in flight — no wall-clock loss, no Vertex AI overload.
_GK_SEMAPHORE = asyncio.Semaphore(1)

GATEKEEPER_PROMPT = """
You are RepoGuard Gatekeeper — a technical project lead enforcing architectural rules on GitLab.

You have been given full context below. Analyze the MR diff against the scope rules, then act using your tools.

---
PROJECT ID: {project_id}
MR IID: {mr_iid}
MR TITLE: {mr_title}
AUTHOR: {mr_author}

SCOPE RULES (.repoguard/scope.json):
{scope_json}

PROJECT README (excerpt):
{readme}

MR DIFF:
{diff}
---

Follow these steps in order:

Step 1 — Determine verdict:
  - APPROVED: diff is clean, no violations
  - REJECTED: any auto_reject_criteria or banned_tech_stack item is introduced
  - NEEDS_REVIEW: forbidden_pattern present but not an auto-reject

Step 2 — You MUST call create_merge_request_note with project_id={project_id}, merge_request_iid={mr_iid} and this exact body (fill in the bracketed placeholders):

## 🛡️ RepoGuard Gatekeeper

| | |
|:--|:--|
| **Verdict** | [use ✅ APPROVED, 🔴 REJECTED, or 🟡 NEEDS REVIEW] |
| **Violated Rule** | [exact quote from scope.json, or `None`] |
| **Reason** | [one sentence explaining the verdict] |
| **Recommendation** | [one sentence action for the developer, or `No action needed`] |

---
*Powered by RepoGuard · Gemini 2.5 Flash*

Step 3 — After posting the comment, reply with exactly one word: APPROVED, REJECTED, or NEEDS_REVIEW.

Take no other actions.
"""


def _parse_verdict(text: str) -> str:
    match = re.search(r"\b(APPROVED|REJECTED|NEEDS_REVIEW)\b", text, re.IGNORECASE)
    return match.group(1).upper() if match else "NEEDS_REVIEW"


async def process_mr(project_id: int, mr_iid: int) -> str:
    try:
        return await _process_mr(project_id, mr_iid)
    except Exception as exc:
        logger.error("Gatekeeper process_mr !%s failed unhandled: %s", mr_iid, exc, exc_info=True)
        return f"error: {exc}"


async def _process_mr(project_id: int, mr_iid: int) -> str:
    # Pre-fetch all GitLab data BEFORE waiting for the AI semaphore.
    # This lets multiple MRs' API calls run concurrently while an AI call is in flight.
    data = await _prefetch(project_id, mr_iid)
    if data is None:
        return "skipped"
    async with _GK_SEMAPHORE:
        return await _run_gatekeeper(project_id, mr_iid, data)


async def _prefetch(project_id: int, mr_iid: int) -> dict | None:
    """Fetch all GitLab data needed for analysis. Returns None if MR should be skipped."""
    gitlab = GitLabClient()
    # ensure_labels and get_mr are independent — run together
    _, mr = await asyncio.gather(
        gitlab.ensure_labels(project_id),
        gitlab.get_mr(project_id, mr_iid),
    )

    existing_labels = mr.get("labels", [])
    if any(label.startswith("repoguard::") for label in existing_labels):
        logger.info("MR !%s already has a repoguard label (%s) — skipping", mr_iid, existing_labels)
        return None

    scope_json, readme, changes_data = await asyncio.gather(
        gitlab.get_file(project_id, ".repoguard/scope.json"),
        gitlab.get_file(project_id, "README.md"),
        gitlab.get_mr_changes(project_id, mr_iid),
    )

    if not scope_json:
        logger.warning("No .repoguard/scope.json found in project %s — skipping", project_id)
        await gitlab.post_mr_comment(
            project_id,
            mr_iid,
            "**RepoGuard:** No `.repoguard/scope.json` found in this repository. Skipping analysis.",
        )
        return None

    # Truncate README — first 800 chars are enough for project context
    readme_excerpt = (readme or "(no README found)")[:800]

    return {
        "gitlab": gitlab,
        "mr": mr,
        "scope_json": scope_json,
        "readme": readme_excerpt,
        "diff": gitlab.format_diff(changes_data.get("changes", [])),
    }


async def _run_gatekeeper(project_id: int, mr_iid: int, data: dict) -> str:
    gitlab = data["gitlab"]
    mr = data["mr"]
    mr_title = mr.get("title", "")
    mr_author = mr.get("author", {}).get("username", "unknown")

    prompt = GATEKEEPER_PROMPT.format(
        project_id=project_id,
        mr_iid=mr_iid,
        mr_title=mr_title,
        mr_author=mr_author,
        scope_json=data["scope_json"],
        readme=data["readme"],
        diff=data["diff"],
    )

    logger.info("Invoking Gatekeeper agent for MR !%s in project %s", mr_iid, project_id)
    verdict = "NEEDS_REVIEW"
    try:
        result = await invoke_agent(prompt, tools=GATEKEEPER_TOOLS)
        verdict = _parse_verdict(result)
        logger.info("Gatekeeper agent completed for MR !%s — verdict: %s", mr_iid, verdict)
    except Exception as exc:
        logger.error(
            "Agent failed for MR !%s: %s — defaulting to NEEDS_REVIEW and applying label",
            mr_iid, exc,
        )

    label_map = {
        "APPROVED": "repoguard::approved",
        "REJECTED": "repoguard::rejected",
        "NEEDS_REVIEW": "repoguard::needs-review",
    }
    label = label_map.get(verdict, "repoguard::needs-review")

    for attempt in range(3):
        try:
            if verdict == "REJECTED":
                await gitlab.update_mr(project_id, mr_iid, state_event="close", add_labels=label)
                logger.info("Closed MR !%s and applied label '%s'", mr_iid, label)
            else:
                await gitlab.update_mr(project_id, mr_iid, add_labels=label)
                logger.info("Applied label '%s' to MR !%s", label, mr_iid)
            break
        except Exception as exc:
            if attempt == 2:
                logger.error("Failed to apply label to MR !%s after 3 attempts: %s", mr_iid, exc)
                break
            wait = 2 ** attempt
            logger.warning("Label apply failed for MR !%s (attempt %d/3), retrying in %ds: %s", mr_iid, attempt + 1, wait, exc)
            await asyncio.sleep(wait)

    await log_gatekeeper(mr_iid, project_id, verdict, mr_title)
    return "ok"
