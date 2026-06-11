#!/bin/bash
set -e

echo "[start.sh] Starting RepoGuard Core Engine..."
exec uv run uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
