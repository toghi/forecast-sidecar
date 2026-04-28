variable "project_id" { type = string }
variable "location" { type = string }
variable "name" {
  type    = string
  default = "forecast-trainer"
}

variable "max_concurrent_dispatches" {
  type        = number
  description = "FR-043: per-env concurrent training cap. 5 in production, 2 in staging."
  validation {
    condition     = var.max_concurrent_dispatches >= 1
    error_message = "max_concurrent_dispatches must be at least 1."
  }
}

variable "max_dispatches_per_second" {
  type        = number
  default     = 1
  description = "Rate cap; default 1/s gives a smooth ramp without bursting."
}

variable "enqueuer_members" {
  type    = list(string)
  default = []
}
