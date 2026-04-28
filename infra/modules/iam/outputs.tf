output "inference_email" {
  value = google_service_account.inference.email
}

output "trainer_email" {
  value = google_service_account.trainer.email
}

output "inference_member" {
  value = "serviceAccount:${google_service_account.inference.email}"
}

output "trainer_member" {
  value = "serviceAccount:${google_service_account.trainer.email}"
}
