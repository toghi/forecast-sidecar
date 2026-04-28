output "network_self_link" { value = google_compute_network.this.self_link }
output "subnet_self_link" { value = google_compute_subnetwork.egress.self_link }
output "subnet_id" { value = google_compute_subnetwork.egress.id }
