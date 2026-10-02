#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

if [[ ! -f .env.dev ]]; then
  echo ".env.dev not found. Copy .env.dev.example and fill it in first." >&2
  exit 1
fi

# --env-file is required here, not just env_file: in the compose YAML -- that only
# injects vars into a container's own process environment. The ${VAR} interpolation
# used for postgres/minio/qdrant's credentials in docker-compose.local.yml is resolved
# by the compose CLI itself, which only reads a plain ".env" by default.
docker compose --env-file .env.dev -f docker-compose.local.yml up -d --build

echo "Waiting for postgres..."
for i in $(seq 1 30); do
  if docker compose --env-file .env.dev -f docker-compose.local.yml exec -T postgres pg_isready -U rag >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "Running migrations..."
docker compose --env-file .env.dev -f docker-compose.local.yml exec -T backend alembic upgrade head

echo "Local stack up. Frontend: http://localhost:3000  Backend: http://localhost:8000"
