#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
# shellcheck disable=SC1091
source "$project_dir/.env"
set +a
export JWT_ALGORITHM=HS256
export JWT_EXPIRE_MINUTES=30

mode="${1:-start}"
case "$mode" in
  start) ;;
  migrate)
    export DB_URL="postgresql+asyncpg://${DATABASE_URL#postgresql://}"
    exec "$project_dir/.venv/bin/python" -m backend.scripts.migrate
    ;;
  check)
    exec "$project_dir/.venv/bin/python" -m pytest -q
    ;;
  *) echo 'usage: ./start.sh [start|migrate|check]' >&2; exit 2 ;;
esac

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKEND_PORT:?BACKEND_PORT is required}"
: "${FRONTEND_PORT:?FRONTEND_PORT is required}"
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY is required}"
: "${OPENROUTER_MODEL:?OPENROUTER_MODEL is required}"
: "${OPENROUTER_BASE_URL:?OPENROUTER_BASE_URL is required}"
[[ "$BACKEND_PORT" != "$FRONTEND_PORT" ]] || { echo 'BACKEND_PORT and FRONTEND_PORT must differ' >&2; exit 1; }
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1024 && port <= 65535 )) || { echo 'Invalid assigned port' >&2; exit 1; }
  ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || { echo "Port $port is occupied" >&2; exit 1; }
done
[[ -x "$project_dir/.venv/bin/python" ]] || { echo 'Python environment is missing' >&2; exit 1; }
[[ -d "$project_dir/web/node_modules" ]] || { echo 'Web dependencies are missing' >&2; exit 1; }

export DB_URL="postgresql+asyncpg://${DATABASE_URL#postgresql://}"
export ENVIRONMENT=development
export HOST=127.0.0.1
export PORT="$BACKEND_PORT"
export CORS_ORIGINS="http://127.0.0.1:$FRONTEND_PORT"
export API_BASE="http://127.0.0.1:$BACKEND_PORT"
export ALLOW_USER_PROVISION=1
export PROVISION_USERNAME="${PROVISION_ADMIN_EMAIL:?PROVISION_ADMIN_EMAIL is required}"
export PROVISION_EMAIL="$PROVISION_ADMIN_EMAIL"
export PROVISION_PASSWORD="${PROVISION_ADMIN_PASSWORD:?PROVISION_ADMIN_PASSWORD is required}"
export PROVISION_ROLE=ADMIN

cd "$project_dir"
"$project_dir/.venv/bin/python" -m backend.scripts.migrate
"$project_dir/.venv/bin/python" -m backend.scripts.provision_user

api_pid=''; ui_pid=''
cleanup() {
  trap - INT TERM EXIT
  [[ -z "$ui_pid" ]] || kill "$ui_pid" 2>/dev/null || true
  [[ -z "$api_pid" ]] || kill "$api_pid" 2>/dev/null || true
  [[ -z "$ui_pid" ]] || wait "$ui_pid" 2>/dev/null || true
  [[ -z "$api_pid" ]] || wait "$api_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

"$project_dir/.venv/bin/uvicorn" backend.main:app --host 127.0.0.1 --port "$BACKEND_PORT" & api_pid=$!
for _ in $(seq 1 240); do
  curl -fsS "http://127.0.0.1:$BACKEND_PORT/readyz" >/dev/null 2>&1 && break
  kill -0 "$api_pid" 2>/dev/null || { wait "$api_pid"; exit $?; }
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$BACKEND_PORT/readyz" >/dev/null
npm --prefix "$project_dir/web" run dev -- --hostname 127.0.0.1 --port "$FRONTEND_PORT" & ui_pid=$!
wait "$api_pid" "$ui_pid"
