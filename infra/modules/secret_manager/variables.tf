variable "project_id" { type = string }

variable "secrets" {
  type        = list(string)
  description = "Secret IDs to create (e.g. [\"sentry-dsn\", \"oidc-allowed-callers\"])."
}

variable "accessor_members" {
  type        = list(string)
  default     = []
  description = "IAM principals granted roles/secretmanager.secretAccessor on EACH secret."
}

variable "labels" {
  type    = map(string)
  default = {}
}
