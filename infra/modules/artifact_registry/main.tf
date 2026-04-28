# T076 — Artifact Registry repository for the container image.
# One repo per env (staging / production); immutable tags.

terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

resource "google_artifact_registry_repository" "this" {
  project       = var.project_id
  location      = var.location
  repository_id = var.repository_id
  format        = "DOCKER"
  description   = "forecast-sidecar container images (${var.repository_id})"

  docker_config {
    immutable_tags = true
  }

  labels = var.labels
}

# Build SA can push; service SAs can pull.
resource "google_artifact_registry_repository_iam_member" "writer" {
  for_each   = toset(var.writer_members)
  project    = google_artifact_registry_repository.this.project
  location   = google_artifact_registry_repository.this.location
  repository = google_artifact_registry_repository.this.repository_id
  role       = "roles/artifactregistry.writer"
  member     = each.value
}

resource "google_artifact_registry_repository_iam_member" "reader" {
  for_each   = toset(var.reader_members)
  project    = google_artifact_registry_repository.this.project
  location   = google_artifact_registry_repository.this.location
  repository = google_artifact_registry_repository.this.repository_id
  role       = "roles/artifactregistry.reader"
  member     = each.value
}
