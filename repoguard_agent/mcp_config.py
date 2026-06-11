from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseConnectionParams, McpOauthClientConfig
from core.config import settings

def get_gitlab_mcp_toolset():
    """Returns the GitLab MCP Toolset configured for RepoGuard."""
    # The official GitLab Duo MCP server uses the standard MCP-over-HTTP (SSE) transport.
    connection_params = SseConnectionParams(
        url=settings.MCP_SERVER_URL
    )

    oauth_config = McpOauthClientConfig(
        client_id=settings.GITLAB_CLIENT_ID,
        client_secret=settings.GITLAB_CLIENT_SECRET,
        auth_url="https://gitlab.com/oauth/authorize",
        token_url="https://gitlab.com/oauth/token",
        user_info_url="https://gitlab.com/oauth/userinfo"
    )
    
    # Provide the token (OAuth preferred, then PAT)
    def header_provider(_context):
        headers = {
            "X-Gitlab-Mcp-Server-Tool-Name-Prefix": "gitlab_",
            "Content-Type": "application/json",
            "User-Agent": "RepoGuard-Agent/1.0"
        }
        
        # Priority 1: OAuth Access Token (The Official Way)
        if settings.GITLAB_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {settings.GITLAB_ACCESS_TOKEN}"
        # Priority 2: Personal Access Token (Legacy/Fallback)
        elif settings.GITLAB_PAT:
            headers["PRIVATE-TOKEN"] = settings.GITLAB_PAT
            
        return headers
    
    return McpToolset(
        connection_params=connection_params,
        oauth_client_config=oauth_config,
        header_provider=header_provider
    )
