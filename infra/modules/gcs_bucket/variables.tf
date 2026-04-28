variable "project_id" {
  type        = string
  description = "GCP project that owns the bucket."
}

variable "name" {
  type        = string
  description = "Globally-unique bucket name."
}

variable "location" {
  type        = string
  description = "GCS location (e.g. EU, europe-west1)."
}

variable "storage_class" {
  type        = string
  default     = "STANDARD"
  description = "Default storage class for new objects."
}

variable "viewer_members" {
  type        = list(string)
  default     = []
  description = "IAM principals (e.g. serviceAccount:foo@bar.iam.gserviceaccount.com) granted roles/storage.objectViewer."
}

variable "admin_members" {
  type        = list(string)
  default     = []
  description = "IAM principals granted roles/storage.objectAdmin (typically the trainer SA)."
}

variable "lifecycle_rules" {
  type        = list(any)
  default     = []
  description = "Optional list of lifecycle rules. Each item: action_type, optional storage_class, age, with_state, num_newer_versions."
}

variable "labels" {
  type        = map(string)
  default     = {}
  description = "Resource labels."
}
