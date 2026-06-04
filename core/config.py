from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    GITLAB_PAT: str
    GITLAB_WEBHOOK_SECRET: str
    GITLAB_API_URL: str = "https://gitlab.com/api/v4"
    GITLAB_PROJECT_ID: int = 0  # Your sandbox-repoguard project ID

    GCP_PROJECT_ID: str
    GCP_LOCATION: str = "us-central1"
    AGENT_ID: str  # Vertex AI Agent Builder agent ID

    class Config:
        env_file = ".env"


settings = Settings()
