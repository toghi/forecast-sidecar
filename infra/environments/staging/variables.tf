variable "project_id" {
  type        = string
  description = "Staging GCP project (e.g. {prefix}-forecast-staging)."
}
variable "region" {
  type    = string
  default = "europe-west1"
}
variable "image_tag" {
  type        = string
  description = "Container tag to deploy (set by CI to ${{ github.sha }})."
}
variable "subnet_cidr" {
  type    = string
  default = "10.20.0.0/24"
}
variable "backend_network_self_link" {
  type        = string
  default     = ""
  description = "Self-link of the calling backend's staging VPC."
}
variable "allowed_callers" {
  type        = list(string)
  description = "Caller SA emails (FR-041)."
}
variable "backend_invoker_member" {
  type        = string
  description = "Calling backend's SA, granted roles/run.invoker on this service."
}
variable "labels" {
  type    = map(string)
  default = { env = "staging", service = "forecast-sidecar" }
}
