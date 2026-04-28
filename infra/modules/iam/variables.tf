variable "project_id" { type = string }

variable "inference_account_id" {
  type    = string
  default = "forecast-sidecar-svc"
}

variable "trainer_account_id" {
  type    = string
  default = "forecast-sidecar-trainer"
}
