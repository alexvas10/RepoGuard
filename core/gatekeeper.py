import logging
from .gitlab_client import GitLabClient
from .agent_client import invoke_agent

logger = logging.getLogger(__name__)

GATEKEEPER_PROMPT = """
You are RepoGuard Gatekeeper — an autonomous architectural compliance agent for a GitLab repository.

You have been given full context below. Your job is to analyze the Merge Request diff against the scope rules and take action using your GitLab tools.

---
PROJECT ID: {project_id}
MR IID: {mr_iid}
MR TITLE: {mr_title}
AUTHOR: {mr_author}

SCOPE RULES (.repoguard/scope.json):
{scope_json}

PROJECT README:
{readme}

MR DIFF:
{diff}
---

INSTRUCTIONS — follow these steps in order:

Step 1: Analyze the diff against the scope rules. Determine one of:
  - APPROVED: diff is clean, no violations
  - REJECTED: one or more auto_reject_criteria or banned_tech_stack violations found
  - NEEDS_REVIEW: potential violation of forbidden_patterns but not an auto-reject

Step 2: Call create_merge_request_note on project_id={project_id}, merge_request_iid={mr_iid} with this exact comment format:

```
## RepoGuard Analysis

**VERDICT:** [APPROVED | REJECTED | NEEDS_REVIEW]
**REASON:** [One sentence explaining the verdict]
**VIOLATED RULE:** [Quote the exact rule from scope.json, or "None"]
**RECOMMENDATION:** [One sentence action for the developer, or "No action needed"]
```

Step 3:
  - If REJECTED: call update_merge_request on project_id={project_id}, iid={mr_iid} with state_event=close and labels="repoguard::rejected"
  - If APPROVED: call update_merge_request on project_id={project_id}, iid={mr_iid} with labels="repoguard::approved"
  - If NEEDS_REVIEW: call update_merge_request on project_id={project_id}, iid={mr_iid} with labels="repoguard::needs-review"

Take no other actions. Do not modify any files.
"""


async def process_mr(project_id: int, mr_iid: int) -> str:
    gitlab = GitLabClient()

    mr = await gitlab.get_mr(project_id, mr_iid)
    mr_title = mr.get("title", "")
    mr_author = mr.get("author", {}).get("username", "unknown")

    scope_json = await gitlab.get_file(project_id, ".repoguard/scope.json")
    if not scope_json:
        logger.warning("No .repoguard/scope.json found in project %s — skipping", project_id)
        await gitlab.post_mr_comment(
            project_id,
            mr_iid,
            "**RepoGuard:** No `.repoguard/scope.json` found in this repository. Skipping analysis.",
        )
        return "skipped: no scope.json"

    readme = await gitlab.get_file(project_id, "README.md") or "(no README found)"

    changes_data = await gitlab.get_mr_changes(project_id, mr_iid)
    diff = gitlab.format_diff(changes_data.get("changes", []))

    prompt = GATEKEEPER_PROMPT.format(
        project_id=project_id,
        mr_iid=mr_iid,
        mr_title=mr_title,
        mr_author=mr_author,
        scope_json=scope_json,
        readme=readme,
        diff=diff,
    )

    logger.info("Invoking agent for MR !%s in project %s", mr_iid, project_id)
    result = await invoke_agent(prompt)
    logger.info("Agent response: %s", result)
    return result
