# T077 — Secret Manager secrets + per-secret accessor IAM.
# Inputs: a list of secret names; the SA that should read them.
# Secret values are NOT managed here — they're populated out of band
# (FR-036 forbids them in Terraform state).

terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

resource "google_secret_manager_secret" "this" {
  for_each  = toset(var.secrets)
  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }

  labels = var.labels
}

# Per-secret accessor binding (NOT project-level — FR-026 least privilege).
resource "google_secret_manager_secret_iam_member" "accessor" {
  for_each = {
    for pair in setproduct(toset(var.secrets), toset(var.accessor_members)) :
    "${pair[0]}:${pair[1]}" => { secret = pair[0], member = pair[1] }
  }
  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.secret].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}
