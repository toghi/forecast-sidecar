#!/usr/bin/env sh
# Cloud Run service entrypoint. Local-only: source `.env` if present.
# In cloud envs Cloud Run injects env vars + Secret Manager bindings,
# so `.env` doesn't exist in the container.
set -e

if [ -f /app/.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /app/.env
  set +a
fi

exec uvicorn forecast_sidecar.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --proxy-headers \
  --forwarded-allow-ips '*'
