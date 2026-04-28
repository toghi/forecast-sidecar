variable "project_id" { type = string }
variable "region" { type = string }

variable "service_name" {
  type    = string
  default = "forecast-sidecar"
}

variable "image" {
  type        = string
  description = "Full image reference, e.g. europe-west1-docker.pkg.dev/.../forecast-sidecar:abc123"
}

variable "service_account_email" {
  type        = string
  description = "Runtime SA (the inference SA from the iam module)."
}

variable "network_self_link" { type = string }
variable "subnet_self_link" { type = string }

variable "cpu" {
  type    = string
  default = "1"
}
variable "memory" {
  type    = string
  default = "1Gi"
}
variable "min_instances" {
  type    = number
  default = 0
}
variable "max_instances" {
  type    = number
  default = 10
}
variable "request_timeout_seconds" {
  type    = number
  default = 60
}

# FR-041: cloud envs MUST set a non-empty allow-list.
variable "allowed_callers" {
  type        = list(string)
  description = "Service-account emails permitted to call /forecast (FR-020). MUST be non-empty in cloud envs."
  validation {
    condition     = length(var.allowed_callers) >= 1
    error_message = "FR-041: allowed_callers must be non-empty in staging and production."
  }
}

variable "expected_audience" {
  type        = string
  description = "OIDC audience this service expects (its own Cloud Run URL)."
}

variable "env_vars" {
  type        = map(string)
  default     = {}
  description = "Non-secret env vars to set on the Cloud Run resource (port, bucket, etc.)."
}

variable "secret_env_vars" {
  type = map(object({
    secret  = string
    version = string
  }))
  default     = {}
  description = "Env vars bound to Secret Manager via secret_key_ref (FR-036). Values never land in Terraform state."
}

variable "invoker_members" {
  type        = list(string)
  default     = []
  description = "IAM principals granted roles/run.invoker (typically just the calling backend's SA)."
}

variable "labels" {
  type    = map(string)
  default = {}
}
