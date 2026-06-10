import os
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from ...tools import (
    get_commits_in_window_tool, get_commit_diff_tool, create_branch_tool,
    revert_commit_tool, create_mr_tool, post_mr_comment_tool,
    log_guardian_event_tool, update_guardian_status_tool
)
from .prompt import agent_instructions

guardian_agent = Agent(
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-preview-05-20"),
    name="guardian_agent",
    description="Performs forensic analysis on production alerts and provisions auto-remediation rollbacks.",
    instruction=agent_instructions,
    tools=[
        FunctionTool(get_commits_in_window_tool),
        FunctionTool(get_commit_diff_tool),
        FunctionTool(create_branch_tool),
        FunctionTool(revert_commit_tool),
        FunctionTool(create_mr_tool),
        FunctionTool(post_mr_comment_tool),
        FunctionTool(log_guardian_event_tool),
        FunctionTool(update_guardian_status_tool)
    ]
)
