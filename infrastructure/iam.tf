locals {
  name = "cost-gate-demo"
}

# ─── IAM Role for GitHub Actions (OIDC keyless auth) ──────────────────────────

resource "aws_iam_role" "github_actions" {
  name = "${local.name}-github-actions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:*"
        }
      }
    }]
  })

  tags = {
    Name = "github-actions-cost-gate"
  }
}

resource "aws_iam_role_policy" "github_actions_policy" {
  name = "cost-gate-ci-policy"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CostExplorerRead"
        Effect   = "Allow"
        Action   = [
          "ce:GetCostAndUsage",
          "ce:GetDimensionValues",
          "ce:GetTags"
        ]
        Resource = "*"
      },
      {
        Sid      = "EKSDescribe"
        Effect   = "Allow"
        Action   = ["eks:DescribeCluster"]
        Resource = module.eks.cluster_arn
      }
    ]
  })
}

# ─── S3 bucket for Terraform remote state (bootstrap) ─────────────────────────
# NOTE: This bucket must exist BEFORE running terraform init with the S3 backend.
# Run the bootstrap script first: scripts/bootstrap_tf_backend.sh

resource "aws_s3_bucket" "tf_state" {
  bucket = var.tf_state_bucket

  lifecycle {
    prevent_destroy = true   # Protect state from accidental deletion
  }

  tags = {
    Name    = "terraform-state"
    Purpose = "cost-gate-tf-state"
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ─── DynamoDB table for Terraform state locking ────────────────────────────────

resource "aws_dynamodb_table" "tf_locks" {
  name         = var.tf_lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name    = "terraform-state-locks"
    Purpose = "cost-gate-tf-locks"
  }
}

# ─── AWS Budget alert for the demo environment ─────────────────────────────────

resource "aws_budgets_budget" "demo_guard" {
  name         = "cost-gate-demo-safety"
  budget_type  = "COST"
  limit_amount = "20"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$cost-aware-gitops-gate"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["ashuj@example.com"]  # Replace with your email
  }
}
