from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseConnectionParams
from core.config import settings

def get_gitlab_mcp_toolset():
    """Returns the GitLab MCP Toolset configured for RepoGuard."""
    # The official GitLab Duo MCP server uses the standard MCP-over-HTTP (SSE) transport.
    connection_params = SseConnectionParams(
        url=settings.MCP_SERVER_URL
    )
    
    # We provide the GitLab PAT via the header_provider
    def header_provider(_context):
        return {
            "Authorization": f"Bearer {settings.GITLAB_PAT}",
            "Content-Type": "application/json",
        }
    
    return McpToolset(
        connection_params=connection_params,
        tool_name_prefix="gitlab_",
        header_provider=header_provider
    )
