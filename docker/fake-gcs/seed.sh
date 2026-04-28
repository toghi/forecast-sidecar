#!/usr/bin/env sh
# Bootstrap the local fake-gcs-server with the bucket the sidecar reads.
# Run as a one-shot compose service after fake-gcs becomes healthy.
set -e

BUCKET="${FORECAST_BUCKET:-local-dev-bucket}"
HOST="${GCS_FAKE_HOST:-http://fake-gcs:4443}"

echo "Seeding fake-gcs at ${HOST}: bucket=${BUCKET}"

# fake-gcs-server's REST API mirrors GCS's. POST /storage/v1/b creates a bucket.
# Idempotent: 409 (already exists) is treated as success.
status=$(curl -s -o /tmp/seed.out -w "%{http_code}" -X POST \
  "${HOST}/storage/v1/b" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${BUCKET}\"}")

case "$status" in
  200|201) echo "  bucket created" ;;
  409)     echo "  bucket already exists (idempotent)" ;;
  *)
    echo "  unexpected status ${status}:"
    cat /tmp/seed.out
    exit 1
    ;;
esac

echo "Seed complete."
