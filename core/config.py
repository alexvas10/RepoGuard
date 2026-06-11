from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    GITLAB_PAT: str = ""
    GITLAB_WEBHOOK_SECRET: str = "dev-secret"
    ALERTS_WEBHOOK_SECRET: str = "dev-secret"
    GITLAB_API_URL: str = "https://gitlab.com/api/v4"
    GITLAB_PROJECT_ID: int = 0  # Your sandbox-repoguard project ID

    GCP_PROJECT_ID: str = ""
    GCP_LOCATION: str = "us-central1"
    GEMINI_MODEL: str = "gemini-2.5-flash"
    MCP_SERVER_URL: str = "https://gitlab.com"
    GOOGLE_GENAI_USE_VERTEXAI: bool = True

    # GitLab OAuth 2.0 (for official MCP)
    GITLAB_CLIENT_ID: str = ""
    GITLAB_CLIENT_SECRET: str = ""
    GITLAB_ACCESS_TOKEN: str = ""
    GITLAB_REFRESH_TOKEN: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

def reload_settings():
    """Reloads settings from environment variables."""
    global settings
    settings = Settings()

def is_configured() -> bool:
    """Check if the critical credentials are set."""
    return bool(settings.GITLAB_PAT and settings.GCP_PROJECT_ID)
