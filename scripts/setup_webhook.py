"""
Registers the RepoGuard webhook on the sandbox-repoguard GitLab project.

Usage:
    python scripts/setup_webhook.py --project-id <ID> --url <CLOUD_RUN_URL>
"""
import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

PAT = os.getenv("GITLAB_PAT")
WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET")
BASE_URL = os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--url", required=True, help="Cloud Run base URL, e.g. https://repoguard-xyz.run.app")
    args = parser.parse_args()

    if not PAT or not WEBHOOK_SECRET:
        print("❌  GITLAB_PAT and GITLAB_WEBHOOK_SECRET must be set in .env")
        sys.exit(1)

    webhook_url = f"{args.url.rstrip('/')}/webhook/gitlab"
    headers = {"Authorization": f"Bearer {PAT}", "Content-Type": "application/json"}

    payload = {
        "url": webhook_url,
        "token": WEBHOOK_SECRET,
        "merge_requests_events": True,
        "push_events": False,
        "enable_ssl_verification": True,
    }

    with httpx.Client() as client:
        resp = client.post(
            f"{BASE_URL}/projects/{args.project_id}/hooks",
            headers=headers,
            json=payload,
        )

    if resp.status_code == 201:
        hook = resp.json()
        print(f"✅  Webhook registered (ID: {hook['id']})")
        print(f"    URL: {webhook_url}")
        print(f"    Triggers: merge_requests_events")
    else:
        print(f"❌  Failed: {resp.status_code} {resp.text}")


if __name__ == "__main__":
    main()
