#!/usr/bin/env bash
#
# Launch the full stack:
#   - Postgres       (expected running on :5432 already)
#   - FastAPI        (backend.main:app on :8080)
#   - Next.js        (web/ on :3000)  unless --no-web
#
# First run creates the DB, seeds ≥15 rows per feature, then starts both.
#
# Flags:
#   --no-seed   skip seeding (keep existing rows)
#   --no-web    start backend only
#
# Env:
#   PORT=8080        backend port
#   WEB_PORT=3000    next.js port
#   DB_URL=postgresql+asyncpg://USER@localhost:5432/investment
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-8080}"
WEB_PORT="${WEB_PORT:-3000}"

SEED=1
WEB=1
for a in "$@"; do
  case "$a" in
    --no-seed) SEED=0 ;;
    --no-web)  WEB=0 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
  esac
done

# ---------- 1. venv ----------
if [[ ! -d .venv ]]; then
  echo "[start.sh] creating .venv"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -e .
fi

# ---------- 2. Postgres DB ----------
DB_NAME="investment"
DB_USER="${USER}"
export DB_URL="${DB_URL:-postgresql+asyncpg://${DB_USER}@localhost:5432/${DB_NAME}}"

if ! pg_isready -q; then
  echo "[start.sh] ERROR: Postgres not running on :5432."
  echo "           Start it with:  brew services start postgresql@14"
  exit 1
fi

if ! psql -lqt | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
  echo "[start.sh] creating database: $DB_NAME"
  createdb "$DB_NAME"
fi

echo "[start.sh] using Postgres: $DB_URL"

# ---------- 3. seed ----------
if [[ "$SEED" == "1" ]]; then
  echo "[start.sh] seeding (drops tables, reseeds ≥15 rows per feature)"
  .venv/bin/python -m backend.seed
else
  echo "[start.sh] skipping seed (--no-seed)"
fi

# ---------- 4. free stale listeners on our ports ----------
free_port() {
  local p="$1"
  local pids
  pids=$(lsof -ti:"$p" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "[start.sh] freeing port $p (killing $pids)"
    kill $pids 2>/dev/null || true
    sleep 1
    pids=$(lsof -ti:"$p" -sTCP:LISTEN 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
      kill -9 $pids 2>/dev/null || true
      sleep 1
    fi
  fi
}
free_port "$PORT"
[[ "$WEB" == "1" ]] && free_port "$WEB_PORT"

# ---------- 5. run ----------
if [[ "$WEB" == "1" ]]; then
  if [[ ! -d web/node_modules ]]; then
    echo "[start.sh] installing web deps"
    (cd web && npm install --no-audit --no-fund --silent)
  fi

  echo "[start.sh] starting backend → http://localhost:${PORT}"
  echo "           web             → http://localhost:${WEB_PORT}"

  .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port "${PORT}" &
  BACKEND_PID=$!
  trap 'echo "[start.sh] shutting down"; kill $BACKEND_PID 2>/dev/null || true; exit 0' INT TERM EXIT

  API_BASE="http://localhost:${PORT}" PORT="${WEB_PORT}" npm --prefix web run dev
else
  echo "[start.sh] starting backend → http://localhost:${PORT}"
  exec .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port "${PORT}"
fi
