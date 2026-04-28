# State backend: per-env GCS bucket, lives in the matching env's project.
# A leaked staging credential reads/writes nothing in production.
terraform {
  required_version = ">= 1.7"
  backend "gcs" {
    # bucket: passed via `terraform init -backend-config="bucket=..."`
    prefix = "forecast-sidecar/staging"
  }
}
