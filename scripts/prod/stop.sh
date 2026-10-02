#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# --env-file: docker-compose.prod.yml's postgres/qdrant credentials and cert
# path are ${VAR:?...} placeholders resolved from .env.prod -- compose parses
# (and requires) them even for `down`, not just `up`.
if [[ "${1:-}" == "--wipe" ]]; then
  docker compose --env-file .env.prod -f docker-compose.prod.yml down -v
  echo "Prod stack stopped and volumes wiped -- Postgres and Qdrant data is gone. Object" \
       "storage is real S3 in prod, so that part is untouched. Make sure you meant this."
else
  docker compose --env-file .env.prod -f docker-compose.prod.yml down
  echo "Prod stack stopped. Volumes kept -- run with --wipe to also drop them."
fi
