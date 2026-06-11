import os
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from ...tools import (
    get_file_tool, get_mr_changes_tool, get_mr_details_tool,
    update_mr_tool, post_mr_comment_tool, log_gatekeeper_event_tool,
    get_wiki_page_tool
)
from ...mcp_config import get_gitlab_mcp_toolset
from .prompt import agent_instructions

gatekeeper_agent = Agent(
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    name="gatekeeper_agent",
    description="Analyzes Merge Requests for architectural compliance and technical violations.",
    instruction=agent_instructions,
    tools=[
        get_gitlab_mcp_toolset(),
        FunctionTool(get_file_tool),
        FunctionTool(get_mr_changes_tool),
        FunctionTool(get_mr_details_tool),
        FunctionTool(update_mr_tool),
        FunctionTool(post_mr_comment_tool),
        FunctionTool(log_gatekeeper_event_tool),
        FunctionTool(get_wiki_page_tool)
    ]
)
