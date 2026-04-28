# T075 — GCS bucket for the artifact tree (forecasts/{company}/{co}/v{N}/).
# Versioning + UBLA + deletion-protection are non-negotiable.

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

resource "google_storage_bucket" "this" {
  project                     = var.project_id
  name                        = var.name
  location                    = var.location
  storage_class               = var.storage_class
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  dynamic "lifecycle_rule" {
    for_each = var.lifecycle_rules
    content {
      action {
        type          = lifecycle_rule.value.action_type
        storage_class = lookup(lifecycle_rule.value, "storage_class", null)
      }
      condition {
        age                = lookup(lifecycle_rule.value, "age", null)
        with_state         = lookup(lifecycle_rule.value, "with_state", null)
        num_newer_versions = lookup(lifecycle_rule.value, "num_newer_versions", null)
      }
    }
  }

  labels = var.labels
}

# Bind read access to the inference SA, write access to the trainer SA.
resource "google_storage_bucket_iam_member" "viewer" {
  for_each = toset(var.viewer_members)
  bucket   = google_storage_bucket.this.name
  role     = "roles/storage.objectViewer"
  member   = each.value
}

resource "google_storage_bucket_iam_member" "admin" {
  for_each = toset(var.admin_members)
  bucket   = google_storage_bucket.this.name
  role     = "roles/storage.objectAdmin"
  member   = each.value
}
