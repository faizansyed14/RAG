#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# --env-file: docker-compose.dev.yml's postgres/minio/qdrant credentials are
# ${VAR:?...} placeholders resolved from .env.dev -- compose parses (and
# requires) them even for `down`, not just `up`.
if [[ "${1:-}" == "--wipe" ]]; then
  docker compose --env-file .env.dev -f docker-compose.dev.yml down -v
  echo "Dev stack stopped and volumes wiped (including indexed documents)."
else
  docker compose --env-file .env.dev -f docker-compose.dev.yml down
  echo "Dev stack stopped. Volumes kept -- run with --wipe to also drop them."
fi
