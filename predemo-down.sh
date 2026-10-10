#!/usr/bin/env bash
# Tear down the predemo bring-up. Stops the web app; agents are LEFT by default
# (cheap, wanted for demo day). Pass --destroy-agents to remove demo-owned agents
# (never the presenter's adopted test-agent) and --revert-adopted to strip the
# canary from the test-agent prompt.
#
# Usage: ./predemo-down.sh [local|docker] [--destroy-agents] [--revert-adopted] [--delete-role]
set -euo pipefail
cd "$(dirname "$0")"

MODE="local"; DESTROY=0; REVERT=""; DELROLE=""
for a in "$@"; do
  case "$a" in
    local|docker) MODE="$a" ;;
    --destroy-agents) DESTROY=1 ;;
    --revert-adopted) REVERT="--revert-adopted" ;;
    --delete-role) DELROLE="--delete-role" ;;
  esac
done
if [[ -f .env ]]; then set -a; . ./.env; set +a; fi

echo "== c0c0n predemo teardown (mode=$MODE) =="
if [[ "$MODE" == "docker" ]]; then
  docker compose down || true
else
  for f in .predemo_backend.pid .predemo_frontend.pid; do
    if [[ -f "$f" ]]; then kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; fi
  done
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "vite preview" 2>/dev/null || true
  echo "-- local app stopped --"
fi

if [[ "$DESTROY" == "1" ]]; then
  echo "-- destroying demo-owned agents --"
  python3 -m venv scripts/.venv
  # shellcheck disable=SC1091
  . scripts/.venv/bin/activate
  pip install -q -r scripts/requirements.txt
  AWS_PROFILE="${AWS_PROFILE:?set AWS_PROFILE}" python3 scripts/destroy_agents.py $REVERT $DELROLE
  deactivate
else
  echo "-- agents left in place (use --destroy-agents to remove) --"
fi
echo "done."
