import os
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from ...tools import create_multiple_files_tool, create_project_tool, list_groups_tool
from ...mcp_config import get_gitlab_mcp_toolset
from .prompt import agent_instructions

architect_agent = Agent(
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    name="architect_agent",
    description="Helps design, create, and scaffold new repositories with best practices.",
    instruction=agent_instructions,
    tools=[
        get_gitlab_mcp_toolset(),
        FunctionTool(create_multiple_files_tool),
        FunctionTool(create_project_tool),
        FunctionTool(list_groups_tool)
    ]
)
