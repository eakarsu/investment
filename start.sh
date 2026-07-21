#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
mode="${1:---api}"

if [[ "${NODE_ENV:-development}" == test ]]; then
  DB_URL="postgresql+asyncpg://${DATABASE_URL#postgresql://}"
  JWT_SECRET_KEY="${JWT_SECRET:-}"
  CORS_ORIGINS="http://127.0.0.1:${FRONTEND_PORT:-}"
  export DB_URL JWT_SECRET_KEY CORS_ORIGINS
fi

for port_name in BACKEND_PORT FRONTEND_PORT; do
  value="${!port_name:-}"
  [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 1024 && value <= 65535 )) || { echo "$port_name must be an explicit integer between 1024 and 65535" >&2; exit 1; }
done
[[ "$BACKEND_PORT" != "$FRONTEND_PORT" ]] || { echo "BACKEND_PORT and FRONTEND_PORT must be different" >&2; exit 1; }
HOST=127.0.0.1
PORT="$BACKEND_PORT"
WEB_PORT="$FRONTEND_PORT"
export HOST PORT WEB_PORT

if [[ ! -x .venv/bin/python ]]; then
  echo "Dependencies are absent. Create the environment and install the locked project explicitly before startup." >&2
  exit 1
fi
if [[ -z "${DB_URL:-}" ]]; then
  echo "DB_URL is required; startup never guesses, creates, migrates, or seeds a database." >&2
  exit 1
fi

case "$mode" in
  --migrate)
    exec .venv/bin/python -m backend.scripts.migrate
    ;;
  --api)
    lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1 && { echo "Assigned API port $BACKEND_PORT is occupied" >&2; exit 1; }
    exec .venv/bin/uvicorn backend.main:app --host "$HOST" --port "$BACKEND_PORT"
    ;;
  --web)
    if [[ ! -d web/node_modules || ! -d web/.next ]]; then
      echo "Web dependencies/build are absent. Run npm ci and npm run build explicitly." >&2
      exit 1
    fi
    lsof -nP -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN >/dev/null 2>&1 && { echo "Assigned web port $FRONTEND_PORT is occupied" >&2; exit 1; }
    exec npm --prefix web run start -- --hostname 127.0.0.1 --port "$FRONTEND_PORT"
    ;;
  --all)
    if [[ ! -d web/node_modules || ! -d web/.next ]]; then
      echo "Web dependencies/build are absent. Run npm ci and npm run build explicitly." >&2
      exit 1
    fi
    for assigned_port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
      lsof -nP -iTCP:"$assigned_port" -sTCP:LISTEN >/dev/null 2>&1 && { echo "Assigned port $assigned_port is occupied" >&2; exit 1; }
    done
    .venv/bin/uvicorn backend.main:app --host "$HOST" --port "$BACKEND_PORT" &
    api_pid=$!
    finish_children() { kill "$api_pid" >/dev/null 2>&1 || true; wait "$api_pid" 2>/dev/null || true; }
    trap finish_children EXIT INT TERM
    npm --prefix web run start -- --hostname 127.0.0.1 --port "$FRONTEND_PORT"
    ;;
  -h|--help)
    echo "Usage: ./start.sh [--api|--web|--all|--migrate]"
    ;;
  *)
    echo "Usage: ./start.sh [--api|--web|--all|--migrate]" >&2
    exit 2
    ;;
esac
