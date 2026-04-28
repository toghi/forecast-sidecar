#!/usr/bin/env sh
# Cloud Run Job entrypoint for the forecast-sidecar trainer.
# Local-only: source `.env` if present (in cloud envs Cloud Run injects env vars
# directly + Secret Manager bindings, so .env doesn't exist in the container).
set -e

if [ -f /app/.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /app/.env
  set +a
fi

exec python -m forecast_sidecar.train_cli "$@"
