variable "aws_region" {
  description = "AWS region to deploy all resources"
  type        = string
  default     = "us-east-1"
}

variable "eks_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.31"
}

variable "node_instance_type" {
  description = "EC2 instance type for EKS worker nodes"
  type        = string
  default     = "t3.micro"
}

variable "github_repo" {
  description = "GitHub repository in owner/repo format (e.g. ashuj/cost-aware-gitops-gate)"
  type        = string
}

variable "tf_state_bucket" {
  description = "S3 bucket name for Terraform remote state"
  type        = string
  default     = "cost-gate-tf-state"
}

variable "tf_lock_table" {
  description = "DynamoDB table name for Terraform state locking"
  type        = string
  default     = "cost-gate-tf-locks"
}
