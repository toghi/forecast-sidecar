# T082 — Cloud Run Job for the trainer. Same image as the service, but
# CMD overrides to `python -m forecast_sidecar.train_cli`. Inbound auth
# does not apply to Jobs; the trainer SA is what holds the GCS write
# permission.

terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

resource "google_cloud_run_v2_job" "this" {
  project  = var.project_id
  location = var.region
  name     = var.job_name

  template {
    template {
      service_account = var.service_account_email
      timeout         = "${var.task_timeout_seconds}s"
      max_retries     = var.max_retries

      vpc_access {
        network_interfaces {
          network    = var.network_self_link
          subnetwork = var.subnet_self_link
        }
        egress = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image   = var.image
        command = ["python"]
        args    = ["-m", "forecast_sidecar.train_cli"]

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }

        dynamic "env" {
          for_each = var.env_vars
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = var.secret_env_vars
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value.secret
                version = env.value.version
              }
            }
          }
        }
      }
    }
  }

  labels = var.labels
}

# Allow the calling backend's SA (or Cloud Tasks dispatcher) to invoke the Job.
resource "google_cloud_run_v2_job_iam_member" "invoker" {
  for_each = toset(var.invoker_members)
  project  = google_cloud_run_v2_job.this.project
  location = google_cloud_run_v2_job.this.location
  name     = google_cloud_run_v2_job.this.name
  role     = "roles/run.invoker"
  member   = each.value
}
