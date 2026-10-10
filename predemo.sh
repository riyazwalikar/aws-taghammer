#!/usr/bin/env bash
# Day-before-demo bring-up: deploys the backend AWS infra (IAM role + both
# Classic agents, updates the test-agent prompt, writes the agent registry) and
# launches the local web app.
#
# Usage:
#   ./predemo.sh [local|docker]     (default: local — the proven, fast path)
#
# Env (from .env or shell): AWS_PROFILE (required), AWS_REGION, APP_PORT (default
# 8090 — 8080 is often taken by Burp), BEDROCK_* optional. Never bakes keys.
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-local}"

# --- load .env if present ---
if [[ -f .env ]]; then set -a; . ./.env; set +a; fi
: "${AWS_PROFILE:?set AWS_PROFILE (in .env or shell) before running}"
: "${AWS_REGION:=us-east-1}"; export AWS_REGION
: "${APP_PORT:=8090}"; export APP_PORT
: "${BACKEND_URL:=http://127.0.0.1:8000}"; export BACKEND_URL
export DB_PATH="${DB_PATH:-$(pwd)/data/demo.sqlite3}"
export FIXTURES_DIR="${FIXTURES_DIR:-$(pwd)/fixtures}"
mkdir -p data fixtures

echo "== c0c0n predemo bring-up (mode=$MODE, profile=$AWS_PROFILE, port=$APP_PORT) =="

# --- 1. AWS infra: role + agents + registry ---
echo "-- deploying AWS agents --"
python3 -m venv scripts/.venv
# shellcheck disable=SC1091
. scripts/.venv/bin/activate
pip install -q -r scripts/requirements.txt
AWS_PROFILE="$AWS_PROFILE" python3 scripts/deploy_agents.py
echo "-- doctor (shopping agent) --"
SHOP_ID=$(python3 -c "import json;print(json.load(open('data/agents.json'))[0]['agent_id'])")
SHOP_ALIAS=$(python3 -c "import json;print(json.load(open('data/agents.json'))[0]['alias_id'])")
AWS_PROFILE="$AWS_PROFILE" python3 scripts/doctor.py --region "$AWS_REGION" \
  --agent-id "$SHOP_ID" --alias-id "$SHOP_ALIAS" --model amazon.nova-pro-v1:0 || \
  echo "(doctor reported issues — review above; continuing)"
deactivate

# --- 2. web app ---
if [[ "$MODE" == "docker" ]]; then
  echo "-- docker compose up --build -d --"
  docker compose up --build -d
  URL="http://127.0.0.1:${APP_PORT}"
else
  echo "-- local backend (uvicorn :8000) --"
  python3 -m venv backend/.venv
  # shellcheck disable=SC1091
  . backend/.venv/bin/activate
  pip install -q -r backend/requirements.txt
  ( AWS_PROFILE="$AWS_PROFILE" AWS_REGION="$AWS_REGION" DB_PATH="$DB_PATH" \
    FIXTURES_DIR="$FIXTURES_DIR" nohup uvicorn app.main:app --app-dir backend \
    --host 127.0.0.1 --port 8000 > /tmp/c0c0n_backend.log 2>&1 & echo $! > .predemo_backend.pid )
  deactivate
  echo "-- local frontend (vite preview :$APP_PORT) --"
  ( cd frontend && [[ -d node_modules ]] || npm install )
  ( cd frontend && npm run build )
  ( cd frontend && APP_PORT="$APP_PORT" BACKEND_URL="$BACKEND_URL" \
    nohup npm run preview > /tmp/c0c0n_frontend.log 2>&1 & echo $! > ../.predemo_frontend.pid )
  URL="http://127.0.0.1:${APP_PORT}"
fi

# --- 3. health check ---
echo "-- waiting for app --"
for i in $(seq 1 30); do
  if curl -sf "${URL}/api/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
if curl -sf "${URL}/api/health" >/dev/null 2>&1; then
  echo "READY: $URL   (agents: $(python3 -c "import json;print(', '.join(a['key'] for a in json.load(open('data/agents.json'))))"))"
else
  echo "WARN: app not answering on ${URL}/api/health — check /tmp/c0c0n_*.log"
  exit 1
fi
