variable "project_id" {
  type        = string
  description = "Production GCP project."
}
variable "region" {
  type    = string
  default = "europe-west1"
}
variable "image_tag" {
  type        = string
  description = "Container tag (semver, set by deploy-production CI to GITHUB_REF_NAME)."
}
variable "subnet_cidr" {
  type    = string
  default = "10.30.0.0/24"
}
variable "backend_network_self_link" {
  type        = string
  default     = ""
  description = "Self-link of the calling backend's production VPC."
}
variable "allowed_callers" {
  type        = list(string)
  description = "Caller SA emails (FR-041)."
}
variable "backend_invoker_member" {
  type        = string
  description = "Calling backend's SA, granted roles/run.invoker."
}
variable "labels" {
  type    = map(string)
  default = { env = "production", service = "forecast-sidecar" }
}
