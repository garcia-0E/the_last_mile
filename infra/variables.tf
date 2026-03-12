// Complete Variable model provided by Docs (https://developer.hashicorp.com/terraform/language/block/variable)
/**
  variable "<LABEL>" {
    type        = <TYPE>
    default     = <DEFAULT_VALUE>
    description = "<DESCRIPTION>"
    sensitive   = <true|false>
    nullable    = <true|false>
    ephemeral   = <true|false>

    validation {
      condition     = <EXPRESSION>
      error_message = "<ERROR_MESSAGE>"
    }
  }
*/

variable "project_id" {
  description = "The GCP project ID"
  type        = string
}

variable "region" {
  description = "The GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "The GCP zone for zonal resources"
  type        = string
  default     = "us-central1-a"
}

variable "environment" {
  description = "Environment name (e.g., dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "labels" {
  description = "Common labels to apply to all resources"
  type        = map(string)
  default     = {}
}

variable "roles" {
  description = "List of IAM permissions to grant to the service account"
  type        = list(string)
  default     = []
}

# variable "vpc_network"{
#   description = "Name of the VPC network to create"
#   type        = string
#   default     = "vpc-network"
# }

# variable "sub_network" {
#   description = "Name of the subnetwork to create"
#   type        = string
#   default     = "my-custom-subnet"
# }

variable "compute_instance_name" {
  description = "Name of the Compute Engine instance"
  type        = string
  default     = "flask-vm"
}

variable "cloud_run_service" {
  description = "Name of the Cloud Run service"
  type        = string
  default     = "cloudrun-service-tfm"
}

variable "deploy_service_account" {
  description = "Name of the service account for deployment"
  type        = string
  default    = "deploy-service-account"
}

variable "github_owner" {
  description = "GitHub repository owner for Cloud Build trigger"
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name for Cloud Build trigger"
  type        = string
}

variable "github_branch" {
  description = "GitHub branch to trigger Cloud Build"
  type        = string
  default     = "main"
}

