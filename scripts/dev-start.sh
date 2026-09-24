#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -f .env.dev ]]; then
  echo ".env.dev not found. Copy .env.dev.example and fill it in first." >&2
  exit 1
fi

docker compose -f docker-compose.dev.yml up -d --build

echo "Waiting for postgres..."
for i in $(seq 1 30); do
  if docker compose -f docker-compose.dev.yml exec -T postgres pg_isready -U rag >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "Running migrations..."
docker compose -f docker-compose.dev.yml exec -T backend alembic upgrade head

echo "Dev stack up. Backend: http://localhost:8000  Frontend: http://localhost:3000"
