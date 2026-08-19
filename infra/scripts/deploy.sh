#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -f .env ]]; then
  echo "Missing ${ROOT_DIR}/.env; create it from .env.production.example" >&2
  exit 1
fi

bash infra/scripts/check-production-env.sh .env
mkdir -p infra/backups/postgres infra/backups/arango

COMPOSE=(
  docker compose
  --env-file .env
  -f infra/compose.yaml
  -f infra/compose.prod.yaml
)

"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" pull --ignore-buildable
"${COMPOSE[@]}" build --pull api
"${COMPOSE[@]}" run --rm --no-deps caddy \
  caddy validate --config /etc/caddy/Caddyfile
"${COMPOSE[@]}" up -d --remove-orphans

for attempt in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready')"; then
    "${COMPOSE[@]}" ps
    echo "QueryPilot is ready."
    exit 0
  fi
  sleep 4
done

"${COMPOSE[@]}" ps
"${COMPOSE[@]}" logs --tail 100 api
echo "Deployment failed readiness checks." >&2
exit 1
