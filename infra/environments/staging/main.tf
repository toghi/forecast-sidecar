terraform {
  required_version = ">= 1.7"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ---------- service accounts (least privilege) ----------------------------
module "iam" {
  source     = "../../modules/iam"
  project_id = var.project_id
}

# ---------- artifact registry (image repo) --------------------------------
module "artifact_registry" {
  source         = "../../modules/artifact_registry"
  project_id     = var.project_id
  location       = var.region
  reader_members = [module.iam.inference_member, module.iam.trainer_member]
  labels         = var.labels
}

# ---------- model bucket --------------------------------------------------
module "gcs_bucket" {
  source         = "../../modules/gcs_bucket"
  project_id     = var.project_id
  name           = "${var.project_id}-forecast-models"
  location       = var.region
  viewer_members = [module.iam.inference_member]
  admin_members  = [module.iam.trainer_member]
  lifecycle_rules = [
    {
      action_type        = "Delete"
      with_state         = "ARCHIVED"
      num_newer_versions = 5
    }
  ]
  labels = var.labels
}

# ---------- secret manager ------------------------------------------------
module "secrets" {
  source     = "../../modules/secret_manager"
  project_id = var.project_id
  secrets    = ["sentry-dsn", "oidc-allowed-callers"]
  accessor_members = [
    module.iam.inference_member,
    module.iam.trainer_member,
  ]
  labels = var.labels
}

# ---------- cloud tasks (trainer queue) -----------------------------------
module "cloud_tasks" {
  source                    = "../../modules/cloud_tasks"
  project_id                = var.project_id
  location                  = var.region
  max_concurrent_dispatches = 2 # FR-043 staging
  enqueuer_members          = [var.backend_invoker_member]
}

# ---------- network -------------------------------------------------------
module "network" {
  source                    = "../../modules/network"
  project_id                = var.project_id
  region                    = var.region
  subnet_cidr               = var.subnet_cidr
  backend_network_self_link = var.backend_network_self_link
}

# ---------- cloud run service (inference) ---------------------------------
module "cloud_run_service" {
  source                = "../../modules/cloud_run_service"
  project_id            = var.project_id
  region                = var.region
  image                 = "${module.artifact_registry.image_prefix}/forecast-sidecar:${var.image_tag}"
  service_account_email = module.iam.inference_email
  network_self_link     = module.network.network_self_link
  subnet_self_link      = module.network.subnet_self_link
  allowed_callers       = var.allowed_callers
  expected_audience     = "https://forecast-sidecar-${var.project_id}.run.app"
  invoker_members       = [var.backend_invoker_member]
  env_vars = {
    PORT                       = "8080"
    FORECAST_BUCKET            = module.gcs_bucket.name
    EXPECTED_AUDIENCE          = "https://forecast-sidecar-${var.project_id}.run.app"
    ALLOWED_CALLERS            = join(",", var.allowed_callers)
    LOG_LEVEL                  = "info"
    SENTRY_ENVIRONMENT         = "staging"
    GIT_SHA                    = var.image_tag
    MODEL_CACHE_SIZE           = "100"
    MODEL_CACHE_TTL_SECONDS    = "3600"
    LATEST_POINTER_TTL_SECONDS = "60"
  }
  secret_env_vars = {
    SENTRY_DSN = { secret = "sentry-dsn", version = "latest" }
  }
  labels = var.labels
}

# ---------- cloud run job (trainer) ---------------------------------------
module "cloud_run_job" {
  source                = "../../modules/cloud_run_job"
  project_id            = var.project_id
  region                = var.region
  image                 = "${module.artifact_registry.image_prefix}/forecast-sidecar:${var.image_tag}"
  service_account_email = module.iam.trainer_email
  network_self_link     = module.network.network_self_link
  subnet_self_link      = module.network.subnet_self_link
  invoker_members       = [var.backend_invoker_member]
  env_vars = {
    FORECAST_BUCKET    = module.gcs_bucket.name
    LOG_LEVEL          = "info"
    SENTRY_ENVIRONMENT = "staging"
    GIT_SHA            = var.image_tag
  }
  secret_env_vars = {
    SENTRY_DSN = { secret = "sentry-dsn", version = "latest" }
  }
  labels = var.labels
}
