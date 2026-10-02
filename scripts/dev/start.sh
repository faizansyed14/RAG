#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

if [[ ! -f .env.dev ]]; then
  echo ".env.dev not found. Copy .env.dev.example and fill it in first." >&2
  exit 1
fi

# nginx bind-mounts this dir (docker-compose.dev.yml) -- a missing cert here
# surfaces as a confusing Docker mount error otherwise. See .env.dev.example's
# EC2 TLS section for how to generate a local cert or get a real one.
tls_cert_dir="$(grep -m1 '^TLS_CERT_DIR=' .env.dev | cut -d= -f2-)"
if [[ -z "$tls_cert_dir" || ! -f "$tls_cert_dir/fullchain.pem" || ! -f "$tls_cert_dir/privkey.pem" ]]; then
  echo "TLS_CERT_DIR (${tls_cert_dir:-unset}) is missing fullchain.pem/privkey.pem." >&2
  echo "See .env.dev.example's EC2 TLS section to generate a local cert or get a real one." >&2
  exit 1
fi

docker compose --env-file .env.dev -f docker-compose.dev.yml up -d --build

echo "Waiting for postgres..."
for i in $(seq 1 30); do
  if docker compose --env-file .env.dev -f docker-compose.dev.yml exec -T postgres pg_isready -U rag >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "Running migrations..."
docker compose --env-file .env.dev -f docker-compose.dev.yml exec -T backend alembic upgrade head

app_url="$(grep -m1 '^NEXT_PUBLIC_API_BASE_URL=' .env.dev | cut -d= -f2-)"
echo "Dev stack up (via nginx): ${app_url:-https://localhost}"
