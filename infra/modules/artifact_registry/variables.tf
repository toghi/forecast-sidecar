variable "project_id" { type = string }
variable "location" { type = string }
variable "repository_id" {
  type        = string
  default     = "forecast-sidecar"
  description = "Repository name."
}
variable "writer_members" {
  type    = list(string)
  default = []
}
variable "reader_members" {
  type    = list(string)
  default = []
}
variable "labels" {
  type    = map(string)
  default = {}
}
