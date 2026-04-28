terraform {
  required_version = ">= 1.7"
  backend "gcs" {
    prefix = "forecast-sidecar/production"
  }
}
