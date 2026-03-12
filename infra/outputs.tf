output "project_id" {
  description = "The GCP project ID"
  value       = var.project_id
}

output "region" {
  description = "The GCP region"
  value       = var.region
}

output "zone" {
  description = "The GCP zone"
  value       = var.zone
}

output "deploy_service_account_email" {
  description = "Email of the deployment service account used by GitHub Actions"
  value       = google_service_account.deploy_service_account.email
}

output "artifact_registry_repo" {
  description = "Artifact Registry repository hostname for Docker images"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.cloud_run_service}"
}
