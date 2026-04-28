# T080 — Network module per FR-039 + research R13:
#   Direct VPC egress on Cloud Run + VPC peering + Cloud NAT
#   + Private Google Access (no Serverless VPC Access connector).

terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

resource "google_compute_network" "this" {
  project                 = var.project_id
  name                    = var.network_name
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "egress" {
  project                  = var.project_id
  name                     = "${var.network_name}-egress"
  ip_cidr_range            = var.subnet_cidr
  region                   = var.region
  network                  = google_compute_network.this.id
  private_ip_google_access = true   # Private Google Access — GCS/SM/JWKS bypass NAT

  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# Router + Cloud NAT for any non-Google outbound (Sentry HTTP ingest, etc.).
resource "google_compute_router" "nat" {
  project = var.project_id
  name    = "${var.network_name}-nat-router"
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "${var.network_name}-nat"
  router                             = google_compute_router.nat.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# VPC peering with the calling backend's VPC.
resource "google_compute_network_peering" "to_backend" {
  count        = var.backend_network_self_link == "" ? 0 : 1
  name         = "${var.network_name}-to-backend"
  network      = google_compute_network.this.self_link
  peer_network = var.backend_network_self_link
  export_custom_routes = false
  import_custom_routes = false
}
