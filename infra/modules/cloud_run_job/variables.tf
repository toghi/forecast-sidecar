variable "project_id" { type = string }
variable "region" { type = string }
variable "job_name" {
  type    = string
  default = "forecast-sidecar-trainer"
}
variable "image" { type = string }
variable "service_account_email" { type = string }

variable "network_self_link" { type = string }
variable "subnet_self_link" { type = string }

variable "cpu" {
  type    = string
  default = "2"
}
variable "memory" {
  type    = string
  default = "4Gi"
}
variable "task_timeout_seconds" {
  type    = number
  default = 1800 # 30 min, per spec §12.2
}
variable "max_retries" {
  type    = number
  default = 1
}

variable "env_vars" {
  type    = map(string)
  default = {}
}

variable "secret_env_vars" {
  type = map(object({
    secret  = string
    version = string
  }))
  default = {}
}

variable "invoker_members" {
  type    = list(string)
  default = []
}

variable "labels" {
  type    = map(string)
  default = {}
}
