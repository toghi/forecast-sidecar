# T081 — Cloud Run inference service. Ingress=internal (FR-038), Direct
# VPC egress (FR-039), env vars from Terraform + secrets via Secret
# Manager `secret_key_ref` (FR-036), least-privilege SA (FR-026), and
# the FR-041 startup gate enforced via Terraform `validation` on
# `var.allowed_callers`.

terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

resource "google_cloud_run_v2_service" "this" {
  project  = var.project_id
  location = var.region
  name     = var.service_name

  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = var.service_account_email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    timeout = "${var.request_timeout_seconds}s"

    vpc_access {
      network_interfaces {
        network    = var.network_self_link
        subnetwork = var.subnet_self_link
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      # Non-secret env vars.
      dynamic "env" {
        for_each = var.env_vars
        content {
          name  = env.key
          value = env.value
        }
      }

      # Secrets bound by reference — the value never lands in Terraform state.
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

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
        timeout_seconds       = 3
      }

      liveness_probe {
        http_get {
          path = "/healthz"
        }
        period_seconds    = 30
        failure_threshold = 3
        timeout_seconds   = 3
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  labels = var.labels
}

# Restrict who can invoke; in practice only the calling backend's SA is
# bound. (Defense in depth — the `internal` ingress already blocks the
# public internet; FR-040 keeps OIDC required even from inside the VPC.)
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  for_each = toset(var.invoker_members)
  project  = google_cloud_run_v2_service.this.project
  location = google_cloud_run_v2_service.this.location
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = each.value
}
