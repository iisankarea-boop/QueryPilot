#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${1:?usage: verify-deployment.sh https://querypilot.example.com}"

LIVE="$(curl --fail --silent --show-error "${BASE_URL%/}/health/live")"
READY="$(curl --fail --silent --show-error "${BASE_URL%/}/health/ready")"

grep -q '"status":"ok"' <<<"${LIVE}"
grep -q '"status":"ready"' <<<"${READY}"

echo "Live: ${LIVE}"
echo "Ready: ${READY}"
