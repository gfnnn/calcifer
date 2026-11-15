#!/usr/bin/env bash
# Build and (re)start Calcifer on a Docker Compose host, refusing to start a
# config that would fail. Use it for the first start and for every update.
#
#   ./deploy/vm/deploy.sh          build and start what is checked out
#   ./deploy/vm/deploy.sh --pull   fetch the latest main first (an update)
#
# Steps: optional git pull -> preflight (scripts.preflight) -> build -> start
# -> wait for /healthz -> tidy old images. The database in ./data is never
# touched; schema changes apply themselves on start-up.
set -euo pipefail

cd "$(dirname "$0")/../.."
say() { printf '\n==> %s\n' "$*"; }

if [ ! -f .env ]; then
  echo "No .env here. Run ./deploy/vm/setup.sh first, then fill it in." >&2
  exit 1
fi

if [ "${1:-}" = "--pull" ]; then
  say "Fetching the latest code"
  before=$(git rev-parse --short HEAD)
  git pull --ff-only
  echo "$before -> $(git rev-parse --short HEAD)"
fi

say "Building the image"
docker compose build

say "Preflight: checking .env"
if ! docker compose run --rm --no-deps -T calcifer python -m scripts.preflight; then
  echo
  echo "Preflight found a problem (marked fail above). Fix .env and run this again." >&2
  echo "The running bot, if any, has not been touched." >&2
  exit 1
fi

say "Starting"
docker compose up -d

say "Waiting for the health check"
for _ in $(seq 1 30); do
  body=$(curl -fsS -m 5 http://127.0.0.1:8080/healthz 2>/dev/null || true)
  if printf '%s' "$body" | grep -q '"status": "ok"'; then
    echo "$body"
    say "Calcifer is up and connected to Discord"
    docker image prune -f >/dev/null 2>&1 || true
    exit 0
  fi
  sleep 4
done

echo "Not healthy after two minutes. Last health answer: ${body:-none}" >&2
echo "Recent logs:" >&2
docker compose logs --tail 40 calcifer >&2
exit 1
