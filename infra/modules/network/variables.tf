variable "project_id" { type = string }

variable "region" { type = string }

variable "network_name" {
  type    = string
  default = "forecast-sidecar"
}

variable "subnet_cidr" {
  type        = string
  description = "Private CIDR for the egress subnet. Must not overlap with the backend VPC's CIDRs."
}

variable "backend_network_self_link" {
  type        = string
  default     = ""
  description = "Self-link of the calling backend's VPC. Empty = peering not provisioned (initial bootstrap)."
}
