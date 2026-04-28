# Non-secret settings for the staging deployment.
# CI overrides image_tag at apply time; everything else is static.

project_id = "REPLACE-ME-staging-project-id"
region     = "europe-west1"

# Default to a placeholder image; CI sets via `-var="image_tag=${{ github.sha }}"`.
image_tag = "latest"

subnet_cidr = "10.20.0.0/24"

# Set after the backend project's VPC is provisioned. Empty = peering not yet
# established; the network module skips the peering resource accordingly.
backend_network_self_link = ""

# FR-041: cloud envs MUST set a non-empty allow-list.
allowed_callers = [
  "toolsname-back-end@REPLACE-ME-staging-project-id.iam.gserviceaccount.com",
]

# Calling backend's SA — gets run.invoker on this service + the trainer job.
backend_invoker_member = "serviceAccount:toolsname-back-end@REPLACE-ME-staging-project-id.iam.gserviceaccount.com"
