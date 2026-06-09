#!/bin/bash
set -e

echo "[start.sh] Starting GitLab MCP sidecar..."
PORT=3000 \
GITLAB_PERSONAL_ACCESS_TOKEN="$GITLAB_PAT" \
USE_STREAMABLE_HTTP=true \
  gitlab-mcp-server &

MCP_PID=$!

# Wait for MCP server to be ready
for i in $(seq 1 15); do
  if curl -sf http://localhost:3000/healthz > /dev/null 2>&1; then
    echo "[start.sh] MCP sidecar ready (pid $MCP_PID)"
    break
  fi
  echo "[start.sh] Waiting for MCP sidecar... ($i/15)"
  sleep 1
done

echo "[start.sh] Starting RepoGuard Core Engine..."
exec uv run uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
