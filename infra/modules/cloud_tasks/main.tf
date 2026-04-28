# T078 — Cloud Tasks queue for trainer fan-out.
# `max_concurrent_dispatches` enforces FR-043 (5 prod / 2 staging).

terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

resource "google_cloud_tasks_queue" "this" {
  project  = var.project_id
  location = var.location
  name     = var.name

  rate_limits {
    max_concurrent_dispatches = var.max_concurrent_dispatches
    max_dispatches_per_second = var.max_dispatches_per_second
  }

  retry_config {
    max_attempts       = 1
    max_retry_duration = "0s"
  }
}

# Allow the enqueuer (typically the calling backend's SA) to add tasks.
resource "google_cloud_tasks_queue_iam_member" "enqueuer" {
  for_each = toset(var.enqueuer_members)
  project  = google_cloud_tasks_queue.this.project
  location = google_cloud_tasks_queue.this.location
  name     = google_cloud_tasks_queue.this.name
  role     = "roles/cloudtasks.enqueuer"
  member   = each.value
}
