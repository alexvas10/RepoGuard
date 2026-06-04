import uuid
import httpx
import google.auth
import google.auth.transport.requests
from .config import settings


def _get_access_token() -> str:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


async def invoke_agent(message: str) -> str:
    """
    Send a message to the Vertex AI Agent Builder agent and return its text response.
    The agent will autonomously call GitLab MCP tools as needed.
    """
    token = _get_access_token()
    session_id = str(uuid.uuid4())

    url = (
        f"https://{settings.GCP_LOCATION}-dialogflow.googleapis.com/v3/"
        f"projects/{settings.GCP_PROJECT_ID}/locations/{settings.GCP_LOCATION}/"
        f"agents/{settings.AGENT_ID}/sessions/{session_id}:detectIntent"
    )

    payload = {
        "queryInput": {
            "text": {"text": message},
            "languageCode": "en-US",
        }
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    resp.raise_for_status()

    data = resp.json()
    messages = data.get("queryResult", {}).get("responseMessages", [])
    for msg in messages:
        if "text" in msg and msg["text"].get("text"):
            return msg["text"]["text"][0]

    return data.get("queryResult", {}).get("text", "Agent produced no text response.")
