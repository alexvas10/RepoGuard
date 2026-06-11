import httpx
import logging
from typing import Dict, Any, Optional
from .config import settings

logger = logging.getLogger(__name__)

GITLAB_BASE_URL = "https://gitlab.com"
DCR_ENDPOINT = f"{GITLAB_BASE_URL}/api/v4/clients/register"
AUTH_ENDPOINT = f"{GITLAB_BASE_URL}/oauth/authorize"
TOKEN_ENDPOINT = f"{GITLAB_BASE_URL}/oauth/token"

async def register_client(redirect_uri: str) -> Dict[str, str]:
    """Performs Dynamic Client Registration with GitLab."""
    payload = {
        "client_name": "RepoGuard",
        "redirect_uris": [redirect_uri],
        "scopes": ["api", "read_repository", "write_repository", "openid"],
        "grant_types": ["authorization_code"]
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(DCR_ENDPOINT, json=payload)
        resp.raise_for_status()
        data = resp.json()
        
    return {
        "client_id": data["client_id"],
        "client_secret": data["client_secret"]
    }

def get_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Generates the GitLab OAuth authorization URL."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": "api read_repository write_repository openid"
    }
    encoded = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{AUTH_ENDPOINT}?{encoded}"

async def exchange_code_for_token(client_id: str, client_secret: str, code: str, redirect_uri: str) -> Dict[str, Any]:
    """Exchanges an authorization code for an access token."""
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_ENDPOINT, data=payload)
        resp.raise_for_status()
        return resp.json()

def save_oauth_to_env(client_id: str = "", client_secret: str = "", access_token: str = "", refresh_token: str = ""):
    """Helper to persist OAuth data to the local .env file."""
    # This is a bit hacky for a prototype but effective
    try:
        with open(".env", "r") as f:
            lines = f.readlines()
        
        updates = {
            "GITLAB_CLIENT_ID": client_id or settings.GITLAB_CLIENT_ID,
            "GITLAB_CLIENT_SECRET": client_secret or settings.GITLAB_CLIENT_SECRET,
            "GITLAB_ACCESS_TOKEN": access_token or settings.GITLAB_ACCESS_TOKEN,
            "GITLAB_REFRESH_TOKEN": refresh_token or settings.GITLAB_REFRESH_TOKEN
        }
        
        new_lines = []
        seen = set()
        for line in lines:
            key = line.split("=")[0] if "=" in line else None
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                seen.add(key)
            else:
                new_lines.append(line)
        
        for key, val in updates.items():
            if key not in seen and val:
                new_lines.append(f"{key}={val}\n")
                
        with open(".env", "w") as f:
            f.writelines(new_lines)
            
        # Update settings object
        settings.GITLAB_CLIENT_ID = updates["GITLAB_CLIENT_ID"]
        settings.GITLAB_CLIENT_SECRET = updates["GITLAB_CLIENT_SECRET"]
        settings.GITLAB_ACCESS_TOKEN = updates["GITLAB_ACCESS_TOKEN"]
        settings.GITLAB_REFRESH_TOKEN = updates["GITLAB_REFRESH_TOKEN"]
        
    except Exception as e:
        logger.error("Failed to save OAuth to .env: %s", e)