# RepoGuard

An autonomous repository guardian that enforces architectural rules on GitLab Merge Requests and provides self-healing remediation for production incidents.

Built for the [Google Cloud Rapid Agent Hackathon](https://devpost.com/software) — GitLab track.
Live demo: [https://repoguard-926140091197.us-central1.run.app](https://repoguard-926140091197.us-central1.run.app)

---

## How It Works

RepoGuard has two autonomous modules that run on Google Cloud Run and watch your GitLab repository 24/7.

### Module 1 — The Gatekeeper

Every time an MR is opened or updated, the Gatekeeper:

1. Fetches the MR diff and your `.repoguard/scope.json` rules in parallel
2. Sends both to Gemini 2.5 Flash via the Google ADK
3. Posts a structured verdict comment directly on the MR via GitLab MCP
4. Applies the appropriate label and closes the MR if rejected

Verdicts:
- **✅ APPROVED** — diff is clean, no violations
- **🔴 REJECTED** — an `auto_reject_criteria` or `banned_tech_stack` item is introduced. MR is closed automatically.
- **🟡 NEEDS REVIEW** — a `forbidden_pattern` is present but doesn't meet auto-reject threshold. MR stays open for a human to decide.

### Module 2 — The Guardian

When a production alert fires, the Guardian:

1. Identifies the breaking commit from the alert timestamp
2. Runs a forensic AI analysis of the commit in parallel with creating a rollback branch and draft MR
3. Posts a structured forensic report as a comment on the rollback MR, including a one-click human approval link
4. On approval, applies the `repoguard::approved-rollback` label and the MR is ready to merge

The full flow from alert to approval link takes under 30 seconds.

---

## Architecture

```
GitLab Webhooks
      │
      ▼
┌─────────────────────────────────────────────────┐
│  Docker Container (Cloud Run)                   │
│                                                 │
│  FastAPI Core Engine           :8080            │
│      │                                          │
│      │  pre-fetches via GitLab REST API         │
│      │  (scope.json, README, MR diff)           │
│      │                                          │
│      ▼                                          │
│  Google ADK — LlmAgent + Runner                 │
│  (Gemini 2.5 Flash via Vertex AI)               │
│      │                                          │
│      ▼                                          │
│  @yoda.digital/gitlab-mcp-server  :3000         │
│  (Node.js sidecar, started by start.sh)         │
│      │                                          │
│      ├── create_merge_request_note              │
│      ├── update_merge_request                   │
│      ├── create_branch                          │
│      ├── revert_commit                          │
│      └── list_commits                           │
└─────────────────────────────────────────────────┘
      │
      ▼
GitLab REST API
```

---

## Customizing the Gatekeeper with `scope.json`

The Gatekeeper's behavior is entirely driven by a `.repoguard/scope.json` file you place in the root of the repository you want to protect. There is no code to change — edit the JSON and the rules take effect on the next MR.

### File location

```
your-repo/
└── .repoguard/
    └── scope.json
```

### Full schema

```json
{
  "project_metadata": {
    "name": "your-project-name",
    "description": "One sentence describing the project's purpose and boundaries."
  },
  "boundaries": {
    "allowed_tech_stack": [],
    "banned_tech_stack": [],
    "forbidden_patterns": []
  },
  "auto_reject_criteria": []
}
```

> **Note on `version`:** if you include a `version` field in `project_metadata`, it is passed to Gemini as context but does not affect verdict logic. It is optional.

### Fields explained

**`allowed_tech_stack`** — Technologies that are explicitly permitted. Gemini uses this to understand what belongs in the codebase. It does not auto-reject anything outside this list, but it informs the verdict reasoning.

```json
"allowed_tech_stack": ["Python", "FastAPI", "SQLite", "httpx", "pydantic"]
```

**`banned_tech_stack`** — Technologies that must never appear. If the diff introduces any of these, the verdict is **REJECTED** and the MR is closed automatically.

```json
"banned_tech_stack": ["React", "Vue", "Angular", "PostgreSQL", "Node.js", "npm"]
```

**`forbidden_patterns`** — Code practices that are discouraged but not hard-blocked. If detected, the verdict is **NEEDS REVIEW** rather than REJECTED, leaving a human to make the final call.

```json
"forbidden_patterns": [
  "Hardcoded API keys or secrets in source code",
  "Synchronous blocking calls inside async endpoint handlers",
  "print() statements used for logging instead of the logging module"
]
```

**`auto_reject_criteria`** — Explicit hard rules. If the diff matches any of these, the verdict is **REJECTED** and the MR is closed automatically. Write these as plain English descriptions of what is not allowed — Gemini interprets them.

```json
"auto_reject_criteria": [
  "Addition of any frontend assets (HTML, CSS, JS, TSX) to this backend-only repository",
  "Addition of package.json, node_modules, or any npm/yarn configuration",
  "Modification of authentication middleware without the 'security-review' label on the MR"
]
```

### Verdict decision logic

| Condition | Verdict |
|-----------|---------|
| Diff is clean, no violations | ✅ APPROVED |
| `banned_tech_stack` item introduced | 🔴 REJECTED (MR closed) |
| `auto_reject_criteria` matched | 🔴 REJECTED (MR closed) |
| `forbidden_pattern` detected (not auto-reject) | 🟡 NEEDS REVIEW |
| Agent error or unparseable response | 🟡 NEEDS REVIEW (safe fallback) |

### Example: Python backend that bans frontend code

```json
{
  "project_metadata": {
    "name": "payments-api",
    "description": "Python FastAPI backend for payment processing. No frontend code."
  },
  "boundaries": {
    "allowed_tech_stack": ["Python", "FastAPI", "PostgreSQL", "SQLAlchemy", "pydantic"],
    "banned_tech_stack": ["React", "Vue", "Angular", "Node.js", "npm", "webpack"],
    "forbidden_patterns": [
      "Hardcoded credentials or API keys",
      "Raw SQL queries outside the repository layer",
      "Disabling SSL verification"
    ]
  },
  "auto_reject_criteria": [
    "Any .js, .ts, .jsx, .tsx, or .css files added to the repository",
    "Changes to payment processing logic without a corresponding test file",
    "Addition of any new outbound HTTP calls to external services not in the approved vendor list"
  ]
}
```

---

## Dashboard

Navigate to `https://<cloud-run-url>/` to open the live dashboard. It auto-refreshes every 10 seconds — no manual reloading needed.

The dashboard shows two panels side by side:

- **Gatekeeper — MR Verdicts:** every MR that was analysed, with its timestamp, MR number (linked to GitLab), title, and colour-coded verdict badge
- **Guardian — Incident Response:** every alert that was processed, with the suspect commit SHA, error type, linked rollback MR, and rollback status

Use the filter buttons to narrow Gatekeeper results by verdict (All / Approved / Rejected / Needs Review) and toggle the sort order between newest-first and oldest-first.

---

## Setup

### Prerequisites

- Python 3.14+
- uv
- Docker (local development only)
- gcloud CLI authenticated to your Google Cloud project
- Google Cloud project with billing enabled
- GitLab account with a Personal Access Token (see step 0)

### 0. Create a GitLab Personal Access Token

Go to [gitlab.com/-/user_settings/personal_access_tokens](https://gitlab.com/-/user_settings/personal_access_tokens) and click **Add new token**.

- **Token name:** `repoguard` (or any name)
- **Expiration:** set as needed
- **Scopes:** check `api`, `read_repository`, `write_repository`

Copy the token — it starts with `glpat-`. This is your `GITLAB_PAT`.

### 1. Clone and configure environment

```bash
git clone https://gitlab.com/alexvas10-group/repoguard.git
cd repoguard
cp .env.example .env
```

Edit `.env`:

```env
GITLAB_PAT=<your-gitlab-pat>
GITLAB_WEBHOOK_SECRET=<choose-a-secret>
ALERTS_WEBHOOK_SECRET=<choose-a-secret>
GITLAB_PROJECT_ID=<id-of-the-repo-you-want-to-protect>
GCP_PROJECT_ID=<your-gcp-project-id>
GCP_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash
MCP_SERVER_URL=http://localhost:3000
```

### 2. Enable Google Cloud APIs

If you don't have a Google Cloud project yet, create one at [console.cloud.google.com](https://console.cloud.google.com/).

```bash
gcloud services enable run.googleapis.com firestore.googleapis.com aiplatform.googleapis.com \
  --project=<GCP_PROJECT_ID>
gcloud firestore databases create --location=us-central1 --project=<GCP_PROJECT_ID>
```

### 3. Add scope.json to the repo you want to protect

Create `.repoguard/scope.json` in the target repository (not the RepoGuard repo itself) and commit it to the default branch. The Gatekeeper fetches it on every MR event.

### 4. Run locally

RepoGuard uses [`@yoda.digital/gitlab-mcp-server`](https://github.com/yoda-digital/gitlab-mcp-server) as its MCP layer. This is a self-hostable, open-source Node.js package — not GitLab's official hosted MCP endpoint. GitLab's own MCP server requires OAuth and runs as a SaaS service that cannot be embedded in a Docker container. The `@yoda.digital` package authenticates with a PAT instead, which makes it possible to run as a sidecar alongside FastAPI.

**You do not need Claude Code to run this project.** The MCP server is a plain Node.js process. In production (Cloud Run), the Dockerfile installs Node.js and the package, and `scripts/start.sh` starts the sidecar automatically before FastAPI — no manual steps required.

For local development, start the sidecar manually first:

```bash
# Option A — Docker (no Node.js install needed)
docker run -e GITLAB_TOKEN=<your-pat> -p 3000:3000 yodadigital/gitlab-mcp-server

# Option B — Node.js directly
npm install -g @yoda.digital/gitlab-mcp-server
GITLAB_PERSONAL_ACCESS_TOKEN=<your-pat> USE_STREAMABLE_HTTP=true PORT=3000 gitlab-mcp-server
```

Then start the API in a separate terminal:

```bash
uv sync
uv run uvicorn main:app --reload
```

### 5. Grant Cloud Run service account permissions

Cloud Run runs as a service account that needs explicit access to Vertex AI and Firestore. Run this once before deploying:

```bash
PROJECT_NUMBER=$(gcloud projects describe <GCP_PROJECT_ID> --format="value(projectNumber)")
SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding <GCP_PROJECT_ID> \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"

gcloud projects add-iam-policy-binding <GCP_PROJECT_ID> \
  --member="serviceAccount:$SA" --role="roles/datastore.user"
```

Without these, Vertex AI calls will fail with `403 PERMISSION_DENIED` and Firestore writes will be silently dropped.

### 6. Deploy to Cloud Run

```bash
gcloud run deploy repoguard \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GITLAB_PAT=...,GITLAB_WEBHOOK_SECRET=...,ALERTS_WEBHOOK_SECRET=...,GCP_PROJECT_ID=...,GITLAB_PROJECT_ID=...
```

### 7. Configure the GitLab webhook

In your GitLab project → Settings → Webhooks:

- **URL:** `https://<cloud-run-url>/webhook/gitlab`
- **Secret token:** value of `GITLAB_WEBHOOK_SECRET`
- **Trigger:** Merge request events ✅

---

## Using the Guardian (Production Alerts)

The Guardian receives alerts from any monitoring system (Datadog, PagerDuty, custom scripts) via a POST endpoint.

### Fire an alert

```bash
curl -X POST https://<cloud-run-url>/webhook/alerts \
  -H "Content-Type: application/json" \
  -H "X-RepoGuard-Token: <ALERTS_WEBHOOK_SECRET>" \
  -d '{
    "timestamp": "2026-06-11T14:32:00Z",
    "error_type": "ZeroDivisionError",
    "severity": "CRITICAL",
    "service": "api-server",
    "stack_trace": "File app/routes/calculate.py, line 42\n    return a / b\nZeroDivisionError: division by zero"
  }'
```

The Guardian searches for commits on the default branch in the 10-minute window before the timestamp. It then creates a rollback branch and forensic MR automatically.

### Approve a rollback

Click the approval link in the forensic comment on the rollback MR, or:

```bash
# List pending rollbacks (to retrieve the token)
curl https://<cloud-run-url>/pending-rollbacks

# Approve
curl -X POST https://<cloud-run-url>/rollback/approve \
  -H "Content-Type: application/json" \
  -d '{"token": "<token>", "mr_iid": <mr_iid>}'
```

### Demo endpoint (bypasses commit search)

```bash
curl -X POST https://<cloud-run-url>/demo/trigger-alert \
  -H "X-RepoGuard-Token: <ALERTS_WEBHOOK_SECRET>"
```

---

## Troubleshooting

**Gatekeeper does nothing when an MR is opened**

Check that `.repoguard/scope.json` exists on the default branch of the target repo. If the file is missing, the Gatekeeper posts a single warning comment on the MR and skips analysis. Also check that the webhook secret in GitLab matches `GITLAB_WEBHOOK_SECRET` exactly — a mismatch returns `403` and the event is silently dropped.

**Gatekeeper skips an MR that was already analysed**

This is intentional. If a `repoguard::` label is already present on the MR (from a previous analysis), the Gatekeeper skips it to prevent a feedback loop where applying a label triggers another webhook event which triggers another analysis. To re-analyse, remove the existing `repoguard::` label from the MR manually and push an update to trigger a new event.

**Guardian returns "no commits found"**

The Guardian searches for commits on the default branch in the 10-minute window before the alert `timestamp`. If no commits fall in that window, it exits without creating a rollback MR. This happens when:
- The timestamp in the alert payload is in the future or too far in the past
- The breaking commit was on a feature branch that was never merged to the default branch

Use `/demo/trigger-alert` for testing — it bypasses the commit search entirely and uses a hardcoded known-bad commit SHA.

**Vertex AI returns `403 PERMISSION_DENIED`**

The Cloud Run service account does not have `roles/aiplatform.user`. Follow step 5 in the setup guide to grant it.

**GitLab labels (`repoguard::approved` etc.) do not appear in the project**

Labels are created automatically on the first webhook event. If they are missing, it means no webhook event has been received yet, or the `GITLAB_PAT` does not have write access to the project. Check that the PAT has the `api` scope and is a member of the project with at least Developer role.

---

## API Reference

| Method | Path | Auth Header | Description |
|--------|------|-------------|-------------|
| `POST` | `/webhook/gitlab` | `X-Gitlab-Token` | Receives GitLab MR events |
| `POST` | `/webhook/alerts` | `X-RepoGuard-Token` | Receives production alert payloads |
| `POST` | `/demo/trigger-alert` | `X-RepoGuard-Token` | Fires a pre-canned demo alert |
| `GET` | `/rollback/confirm/{token}/{mr_iid}` | — | Human approval confirmation page |
| `POST` | `/rollback/approve` | — | Approves a pending rollback (JSON body) |
| `GET` | `/pending-rollbacks` | — | Lists rollbacks awaiting approval |
| `GET` | `/` | — | Live dashboard (auto-refreshes every 10s) |
| `GET` | `/health` | — | Health check |

---

## License

Apache 2.0
