# ============================================================================
# ECR Repository for AgentCore container image
# ============================================================================

resource "aws_ecr_repository" "agentcore" {
  name                 = "${var.project_name}-agentcore"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${var.project_name}-agentcore"
    Environment = "production"
  }
}

output "ecr_repository_url" {
  description = "ECR repository URL for AgentCore image"
  value       = aws_ecr_repository.agentcore.repository_url
}
