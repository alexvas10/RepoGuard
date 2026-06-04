"""
Pushes all sandbox-repoguard content to your GitLab project and creates
the demo branches. Run this once after creating the sandbox-repoguard project.

Usage:
    python scripts/setup_sandbox.py --project-id <ID>
"""
import argparse
import base64
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

PAT = os.getenv("GITLAB_PAT")
BASE_URL = os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4")


def headers():
    return {"Authorization": f"Bearer {PAT}", "Content-Type": "application/json"}


def upsert_file(client: httpx.Client, project_id: int, path: str, content: str, branch: str, message: str):
    encoded_path = path.replace("/", "%2F")
    url = f"{BASE_URL}/projects/{project_id}/repository/files/{encoded_path}"

    # check if file exists
    check = client.get(url, headers=headers(), params={"ref": branch})
    payload = {
        "branch": branch,
        "content": content,
        "commit_message": message,
    }
    if check.status_code == 200:
        resp = client.put(url, headers=headers(), json=payload)
    else:
        resp = client.post(url, headers=headers(), json=payload)

    if resp.status_code not in (200, 201):
        print(f"  ⚠  Failed to write {path}: {resp.status_code} {resp.text[:100]}")
    else:
        print(f"  ✅  {branch}/{path}")


def ensure_branch(client: httpx.Client, project_id: int, branch: str, ref: str = "main"):
    url = f"{BASE_URL}/projects/{project_id}/repository/branches"
    resp = client.post(url, headers=headers(), json={"branch": branch, "ref": ref})
    if resp.status_code == 400 and "already exists" in resp.text:
        print(f"  (branch {branch} already exists)")
    elif resp.status_code == 201:
        print(f"  Created branch: {branch}")
    else:
        print(f"  ⚠  Branch {branch}: {resp.status_code} {resp.text[:80]}")


def read_local(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True, type=int)
    args = parser.parse_args()
    project_id = args.project_id

    if not PAT:
        print("❌  GITLAB_PAT not set in .env")
        sys.exit(1)

    base = "sandbox-content"

    with httpx.Client(timeout=30) as client:
        # ── Main branch: core app files ──────────────────────────────────────
        print("\n── Pushing main branch files ──")
        main_files = {
            "README.md": f"{base}/README.md",
            ".repoguard/scope.json": f"{base}/.repoguard/scope.json",
            "app/__init__.py": f"{base}/app/__init__.py",
            "app/main.py": f"{base}/app/main.py",
            "app/routes/__init__.py": f"{base}/app/routes/__init__.py",
            "app/routes/calculate.py": f"{base}/app/routes/calculate.py",
        }
        for gl_path, local_path in main_files.items():
            upsert_file(client, project_id, gl_path, read_local(local_path), "main", f"chore: init {gl_path}")

        # ── feature/good-refactor branch ─────────────────────────────────────
        print("\n── Creating feature/good-refactor branch ──")
        ensure_branch(client, project_id, "feature/good-refactor")
        upsert_file(
            client, project_id,
            "app/routes/calculate.py",
            read_local(f"{base}/demo-branches/good-refactor/app/routes/calculate.py"),
            "feature/good-refactor",
            "refactor: improve calculate endpoint with multiply and 400 response",
        )

        # ── feature/bad-frontend-creep branch ────────────────────────────────
        print("\n── Creating feature/bad-frontend-creep branch ──")
        ensure_branch(client, project_id, "feature/bad-frontend-creep")
        upsert_file(
            client, project_id,
            "frontend/App.jsx",
            read_local(f"{base}/demo-branches/bad-frontend-creep/frontend/App.jsx"),
            "feature/bad-frontend-creep",
            "feat: add React frontend",
        )
        upsert_file(
            client, project_id,
            "package.json",
            read_local(f"{base}/demo-branches/bad-frontend-creep/package.json"),
            "feature/bad-frontend-creep",
            "chore: add package.json",
        )

        # ── Guardian demo: push the "breaking" commit to main ────────────────
        print("\n── Pushing breaking commit to main (Guardian demo) ──")
        upsert_file(
            client, project_id,
            "app/routes/calculate.py",
            read_local(f"{base}/demo-branches/bad-calculate/app/routes/calculate.py"),
            "main",
            "fix: remove redundant zero check in divide (BREAKING — for Guardian demo)",
        )

        print("\n✅  Sandbox setup complete.")
        print(f"\nNext: open MRs from these branches in your GitLab UI:")
        print(f"  - feature/good-refactor  → should get APPROVED")
        print(f"  - feature/bad-frontend-creep → should get REJECTED")
        print(f"\nFor the Guardian demo, fire: POST /demo/trigger-alert")


if __name__ == "__main__":
    main()
