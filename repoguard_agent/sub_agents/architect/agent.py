import os
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from ...tools import create_multiple_files_tool
from ...mcp_config import get_gitlab_mcp_toolset
from .prompt import agent_instructions

architect_agent = Agent(
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-preview-05-20"),
    name="architect_agent",
    description="Helps design and scaffold new repositories with best practices.",
    instruction=agent_instructions,
    tools=[
        get_gitlab_mcp_toolset(),
        FunctionTool(create_multiple_files_tool)
    ]
)
