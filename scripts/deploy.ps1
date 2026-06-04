# Deploy RepoGuard to Google Cloud Run
# Usage: .\scripts\deploy.ps1
#
# Prerequisites:
#   gcloud auth login
#   gcloud config set project <YOUR_PROJECT_ID>

param(
    [string]$Region = "us-central1",
    [string]$ServiceName = "repoguard"
)

# Load .env values for Cloud Run env vars
$envFile = ".env"
if (-not (Test-Path $envFile)) {
    Write-Error ".env file not found. Copy .env.example to .env and fill in values."
    exit 1
}

$envVars = @{}
Get-Content $envFile | Where-Object { $_ -match "^\s*[^#]" -and $_ -match "=" } | ForEach-Object {
    $parts = $_ -split "=", 2
    $key = $parts[0].Trim()
    $value = $parts[1].Trim()
    $envVars[$key] = $value
}

$required = @("GITLAB_PAT", "GITLAB_WEBHOOK_SECRET", "GCP_PROJECT_ID", "AGENT_ID")
foreach ($key in $required) {
    if (-not $envVars.ContainsKey($key) -or $envVars[$key] -eq "") {
        Write-Error "Missing required .env value: $key"
        exit 1
    }
}

$envString = ($envVars.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ","

Write-Host "Deploying $ServiceName to Cloud Run ($Region)..." -ForegroundColor Cyan

gcloud run deploy $ServiceName `
    --source . `
    --region $Region `
    --allow-unauthenticated `
    --set-env-vars $envString `
    --memory 512Mi `
    --timeout 300

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n✅  Deployed. Get your service URL:" -ForegroundColor Green
    gcloud run services describe $ServiceName --region $Region --format "value(status.url)"
} else {
    Write-Error "Deployment failed."
}
