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

##### Pub/Sub #####

# resource "google_pubsub_topic" "file_processing" {
#   name    = "file-processing-topic"
#   project = var.project_id

# }

# resource "google_pubsub_subscription" "file_processing_subscription" {
#   name  = "file-processing-subscription"
#   topic = google_pubsub_topic.file_processing.name
#   project = var.project_id
# }

# resource "google_storage_bucket" "bucket" {
#   name                        = "${var.project_id}-gcf-source-pubsub"
#   location                    = "US"
#   uniform_bucket_level_access = true
#   project                     = var.project_id
# }

# resource "google_storage_bucket_object" "function-source" {
#   name   = "sample_function_py.zip"
#   bucket = google_storage_bucket.bucket.name
#   source = "../../helpers/sample_function_py.zip"
# }

# module "pubsub" {
#   source  = "terraform-google-modules/pubsub/google"
#   version = "~> 7.0"

#   topic      = "function2-topic"
#   project_id = var.project_id
# }

# module "cloud_functions2" {
#   source  = "GoogleCloudPlatform/cloud-functions/google"
#   version = "~> 0.7"

#   project_id    = var.project_id
#   function_name = "function2-pubsub-trigger-py"
#   location      = var.location
#   runtime       = "python310"
#   entrypoint    = "hello_http"
#   storage_source = {
#     bucket     = google_storage_bucket.bucket.name
#     object     = google_storage_bucket_object.function-source.name
#     generation = null
#   }
#   event_trigger = {
#     trigger_region        = "us-central1"
#     event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
#     service_account_email = null
#     pubsub_topic          = module.pubsub.id
#     retry_policy          = "RETRY_POLICY_RETRY"
#     event_filters         = null
#   }
# }

#### BigQuery #####
resource "google_bigquery_dataset" "tfm_dataset_apollo_contacts" {
  dataset_id = "tfm_dataset_apollo_contacts"
  project    = var.project_id
  location   = var.region
}

resource "google_bigquery_table" "apollo_contacts_normalized" {
  table_id   = "apollo_contacts_normalized"
  dataset_id = google_bigquery_dataset.tfm_dataset_apollo_contacts.dataset_id
  project    = var.project_id
}

#### Cloud SQL #####
resource "google_sql_database_instance" "tfm_db_instance" {
  name             = var.tfm_db_instance
  database_version = "POSTGRES_18"
  region           = var.region
  project          = var.project_id
  deletion_protection = true

  settings {
    tier = "db-f1-micro"
  }
}