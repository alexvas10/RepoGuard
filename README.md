# RepoGuard

An autonomous repository guardian that enforces architectural rules on GitLab Merge Requests and provides self-healing remediation for production incidents.

Built for the [Google Cloud Rapid Agent Hackathon](https://devpost.com/software) — GitLab track. https://gitlab.com/alexvas10/repoguard (currently gone)

## Architecture

```
GitLab Webhooks
      │
      ▼
RepoGuard Core Engine  (Cloud Run / FastAPI)
      │
      │  pre-fetches: scope.json, README, MR diff
      │
      ▼
Vertex AI Agent Builder  (Gemini + GitLab MCP)
      │
      └── GitLab Native MCP  (https://gitlab.com/api/v4/mcp)
              │
              ├── create_merge_request_note
              ├── update_merge_request
              ├── create_branch
              ├── create_commit / revert_commit
              └── list_commits
```

## Modules

### Module 1 — The Gatekeeper
Monitors every opened or updated MR. Fetches `.repoguard/scope.json` and the MR diff, then invokes the Gemini agent to:
- Analyze the diff for architectural violations
- Post a structured verdict comment (APPROVED / REJECTED / NEEDS_REVIEW)
- Close the MR and label it if it violates scope

### Module 2 — The Guardian
Receives production alerts. Performs forensic analysis to identify the breaking commit, then invokes the Gemini agent to:
- Create an `emergency/rollback-{SHA}` branch
- Open a draft `[AUTO-REMEDIATION]` MR
- Wait for human approval before the MR can be merged

## Setup

### 1. Prerequisites
- Google Cloud project with Vertex AI Agent Builder enabled
- GitLab account with a PAT (`api` + `read_repository` scopes)
- GitLab MCP enabled (Beta features on in GitLab settings)

### 2. Environment
```bash
cp .env.example .env
# fill in all values
```

### 3. Create the Vertex AI Agent
1. Go to Vertex AI Agent Builder → Create Agent
2. Add GitLab MCP Tool:
   - Endpoint: `https://gitlab.com/api/v4/mcp`
   - Auth: Custom Header `Authorization: Bearer <GITLAB_PAT>`
3. Paste contents of `system_prompt.md` as System Instructions
4. Copy the Agent ID into your `.env` as `AGENT_ID`

### 4. Set `DEMO_PROJECT_ID` in `main.py`
Find your sandbox-repoguard project ID in GitLab (Settings → General) and set `DEMO_PROJECT_ID` at the top of `main.py`.

### 5. Run locally
```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

### 6. Deploy to Cloud Run
```bash
gcloud run deploy repoguard \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GITLAB_PAT=...,GITLAB_WEBHOOK_SECRET=...,GCP_PROJECT_ID=...,AGENT_ID=...
```

### 7. Configure GitLab Webhook
In `sandbox-repoguard` → Settings → Webhooks:
- URL: `https://<cloud-run-url>/webhook/gitlab`
- Secret token: same as `GITLAB_WEBHOOK_SECRET`
- Trigger: Merge request events

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhook/gitlab` | Receives GitLab MR events |
| `POST` | `/webhook/alerts` | Receives production alert payloads |
| `POST` | `/rollback/approve` | Approves a pending rollback MR |
| `POST` | `/demo/trigger-alert` | Fires a pre-canned demo alert |
| `GET`  | `/pending-rollbacks` | Lists rollbacks awaiting approval |
| `GET`  | `/health` | Health check |

## Demo: Triggering the Guardian

```bash
# Option 1: pre-canned demo endpoint
curl -X POST https://<cloud-run-url>/demo/trigger-alert

# Option 2: custom alert
curl -X POST https://<cloud-run-url>/webhook/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2026-06-08T14:32:00Z",
    "error_type": "ZeroDivisionError",
    "severity": "CRITICAL",
    "service": "api-server",
    "stack_trace": "File '\''app/routes/calculate.py'\'', line 42, in divide\n    return a / b\nZeroDivisionError: division by zero"
  }'

# Approve the rollback (use token from /pending-rollbacks)
curl -X POST https://<cloud-run-url>/rollback/approve \
  -H "Content-Type: application/json" \
  -d '{"token": "<token>", "mr_iid": <mr_iid>}'
```

## License
MIT
