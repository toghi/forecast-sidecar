# T079 — Service accounts for the inference service and the trainer.
# Project-level role grants are kept minimal (FR-026); resource-level
# bindings (bucket, secrets, queue) live in their respective modules.

terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

resource "google_service_account" "inference" {
  project      = var.project_id
  account_id   = var.inference_account_id
  display_name = "forecast-sidecar inference service"
  description  = "Cloud Run service runtime SA for forecast-sidecar (read-only on the model bucket)."
}

resource "google_service_account" "trainer" {
  project      = var.project_id
  account_id   = var.trainer_account_id
  display_name = "forecast-sidecar trainer"
  description  = "Cloud Run Job runtime SA for the trainer (read+write on the model bucket)."
}

# Cloud Run logs / metrics / trace writers — project-level minimum required
# for the runtime to emit structlog → Cloud Logging.
resource "google_project_iam_member" "inference_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.inference.email}"
}
resource "google_project_iam_member" "inference_metrics" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.inference.email}"
}
resource "google_project_iam_member" "trainer_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.trainer.email}"
}
resource "google_project_iam_member" "trainer_metrics" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.trainer.email}"
}
