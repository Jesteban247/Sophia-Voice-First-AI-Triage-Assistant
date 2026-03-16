# ============================================================================
# Terraform Variables
# ============================================================================

variable "account_id" {
  description = "AWS Account ID"
  type        = string
}

variable "aws_region" {
  description = "AWS Region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "sonic"
}

variable "runtime_arn" {
  description = "AgentCore Runtime ARN (set by deploy script)"
  type        = string
  default     = ""
}

variable "images_bucket_name" {
  description = "Optional override for images bucket name"
  type        = string
  default     = ""
}

variable "qualifier" {
  description = "Runtime qualifier"
  type        = string
  default     = "DEFAULT"
}

variable "vitallens_api_key" {
  description = "VitalLens API key for server-side proxy"
  type        = string
  default     = ""
}
