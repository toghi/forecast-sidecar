project_id = "REPLACE-ME-production-project-id"
region     = "europe-west1"

image_tag = "v0.0.0" # CI sets via -var; placeholder ensures plan works locally

subnet_cidr = "10.30.0.0/24"

backend_network_self_link = ""

allowed_callers = [
  "toolsname-back-end@REPLACE-ME-production-project-id.iam.gserviceaccount.com",
]

backend_invoker_member = "serviceAccount:toolsname-back-end@REPLACE-ME-production-project-id.iam.gserviceaccount.com"
