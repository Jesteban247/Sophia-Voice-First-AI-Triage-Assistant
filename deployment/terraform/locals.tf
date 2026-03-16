locals {
  images_bucket_name = var.images_bucket_name != "" ? var.images_bucket_name : "${var.project_name}-images-${var.account_id}"
}
