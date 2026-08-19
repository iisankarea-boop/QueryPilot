#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-${ROOT_DIR}/infra/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ "${BACKUP_ROOT}" == "/" || "${BACKUP_ROOT}" == "${ROOT_DIR}" ]]; then
  echo "Refusing unsafe backup root: ${BACKUP_ROOT}" >&2
  exit 1
fi
if ! [[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]]; then
  echo "BACKUP_RETENTION_DAYS must be a non-negative integer" >&2
  exit 1
fi

mkdir -p "${BACKUP_ROOT}/postgres" "${BACKUP_ROOT}/arango"
BACKUP_ROOT="$(cd "${BACKUP_ROOT}" && pwd)"
export BACKUP_ROOT

COMPOSE=(
  docker compose
  --env-file "${ROOT_DIR}/.env"
  -f "${ROOT_DIR}/infra/compose.yaml"
  -f "${ROOT_DIR}/infra/compose.prod.yaml"
)

POSTGRES_FILE="querypilot-${TIMESTAMP}.dump"
ARANGO_DIR="all-databases-${TIMESTAMP}"

"${COMPOSE[@]}" exec -T -e BACKUP_FILE="${POSTGRES_FILE}" postgres \
  sh -ceu 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "/backups/$BACKUP_FILE"'

"${COMPOSE[@]}" exec -T -e BACKUP_DIR="${ARANGO_DIR}" arangodb \
  sh -ceu 'arangodump \
    --server.endpoint tcp://127.0.0.1:8529 \
    --server.username root \
    --server.password "$ARANGO_ROOT_PASSWORD" \
    --all-databases true \
    --output-directory "/backups/$BACKUP_DIR" \
    --overwrite true'

find "${BACKUP_ROOT}/postgres" -maxdepth 1 -type f -name 'querypilot-*.dump' \
  -mtime "+${RETENTION_DAYS}" -delete
find "${BACKUP_ROOT}/arango" -mindepth 1 -maxdepth 1 -type d \
  -name 'all-databases-*' -mtime "+${RETENTION_DAYS}" -exec rm -rf -- {} +

echo "Backup complete: ${POSTGRES_FILE}, ${ARANGO_DIR}"
