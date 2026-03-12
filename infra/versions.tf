terraform {
  required_version = ">= 1.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
  }

  # Uncomment and configure for remote state
  # backend "gcs" {
  #   bucket = "your-terraform-state-bucket"
  #   prefix = "terraform/state"
  # }
}
