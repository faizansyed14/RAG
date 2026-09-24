#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ "${1:-}" == "--wipe" ]]; then
  docker compose -f docker-compose.dev.yml down -v
  echo "Dev stack stopped and volumes wiped (including indexed documents)."
else
  docker compose -f docker-compose.dev.yml down
  echo "Dev stack stopped. Volumes kept -- run with --wipe to also drop them."
fi
