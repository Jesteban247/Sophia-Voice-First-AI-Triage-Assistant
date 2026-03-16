# ============================================================================
# S3 Bucket for medical images and report uploads
# ============================================================================

resource "aws_s3_bucket" "images" {
  bucket        = local.images_bucket_name
  force_destroy = true

  tags = {
    Name        = "${var.project_name}-images"
    Environment = "production"
  }
}

resource "aws_s3_bucket_public_access_block" "images" {
  bucket = aws_s3_bucket.images.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_cors_configuration" "images" {
  bucket = aws_s3_bucket.images.id

  cors_rule {
    allowed_methods = ["GET", "PUT", "POST"]
    allowed_origins = ["*"]
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

output "images_bucket_name" {
  description = "S3 bucket name for medical images and reports"
  value       = aws_s3_bucket.images.id
}
