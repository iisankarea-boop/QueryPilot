#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${1:-.env}"
EXAMPLE_HASH='DEMO_PASSWORD_HASH='"'"'$2a$14$jPZRLNoQs7g8nprMeeQmtuZFJ/xgof2Y4HGr21TcHO37XUb/mbpTK'"'"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing production environment file: ${ENV_FILE}" >&2
  exit 1
fi

if grep -Eq '^[A-Z0-9_]+=.*replace-me' "${ENV_FILE}"; then
  echo "Production environment still contains replace-me values." >&2
  exit 1
fi
if grep -Fxq 'DOMAIN=querypilot.example.com' "${ENV_FILE}"; then
  echo "Production DOMAIN still uses the example value." >&2
  exit 1
fi
if grep -Fxq 'ACME_EMAIL=owner@example.com' "${ENV_FILE}"; then
  echo "Production ACME_EMAIL still uses the example value." >&2
  exit 1
fi
if grep -Fxq "${EXAMPLE_HASH}" "${ENV_FILE}"; then
  echo "Production Basic Auth password hash still uses the example value." >&2
  exit 1
fi

echo "Production environment placeholders: clear"
