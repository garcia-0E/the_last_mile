# Main Terraform configuration for GCP

##### Service Account #####

resource "google_service_account" "deploy_service_account" {
  account_id   = "tlm-deploy-sa"
  display_name = "The Last Mile Deployment Service Account"
  project      = var.project_id
}

resource "google_project_iam_member" "deploy_sa_permissions" {
  for_each = toset(var.roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.deploy_service_account.email}"
}

##### Artifact Registry #####

resource "google_artifact_registry_repository" "cloud_run_repo" {
  location      = var.region
  repository_id = var.cloud_run_service
  description   = "Docker repository for Cloud Run services"
  format        = "DOCKER"
  project       = var.project_id
}

# Cloud Run service is managed by GitHub Actions (deploy-cloudrun action),
# not Terraform. This avoids the bootstrap problem of needing a container
# image to exist before the service can be created.

##### Workload Identity Federation (GitHub Actions) #####

# resource "google_iam_workload_identity_pool" "github" {
#   workload_identity_pool_id = "github"
#   display_name              = "GitHub Actions Pool"
#   description               = "Workload Identity Pool for GitHub Actions"
#   project                   = var.project_id
# }

# resource "google_iam_workload_identity_pool_provider" "github_actions" {
#   workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
#   workload_identity_pool_provider_id = "github-actions-provider"
#   display_name                       = "GitHub Actions Provider"
#   project                            = var.project_id

#   attribute_mapping = {
#     "google.subject"       = "assertion.sub"
#     "attribute.actor"      = "assertion.actor"
#     "attribute.repository" = "assertion.repository"
#   }

#   attribute_condition = "assertion.repository_owner == '${var.github_owner}'"

#   oidc {
#     issuer_uri = "https://token.actions.githubusercontent.com"
#   }
# }

# Allow GitHub Actions WIF to impersonate the deploy service account
# resource "google_service_account_iam_member" "wif_sa_binding" {
#   service_account_id = google_service_account.deploy_service_account.name
#   role               = "roles/iam.workloadIdentityUser"
#   member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
# }

##### Pub/Sub #####

resource "google_pubsub_topic" "file_processing" {
  name    = "file-processing-topic"
  project = var.project_id
  
}
