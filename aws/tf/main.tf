# SPDX-FileCopyrightText: 2025 Caution SEZC
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial

terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

# IAM User for Caution managed on-prem deployments
resource "aws_iam_user" "caution_deploy" {
  name = var.iam_user_name

  tags = {
    Purpose   = "caution-managed-onprem"
    ManagedBy = "managed-onprem-setup"
  }
}

# Policy document with permissions needed for enclave deployments
data "aws_iam_policy_document" "caution_deploy" {
  # EC2 permissions for instance management
  statement {
    sid = "EC2InstanceManagement"
    actions = [
      "ec2:RunInstances",
      "ec2:TerminateInstances",
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceStatus",
      "ec2:StartInstances",
      "ec2:StopInstances",
      "ec2:RebootInstances",
      "ec2:DescribeImages",
      "ec2:DescribeInstanceTypes",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # Security group management
  statement {
    sid = "SecurityGroupManagement"
    actions = [
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:DescribeSecurityGroups",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
    ]
    resources = ["*"]
  }

  # Elastic IP management
  statement {
    sid = "ElasticIPManagement"
    actions = [
      "ec2:AllocateAddress",
      "ec2:ReleaseAddress",
      "ec2:AssociateAddress",
      "ec2:DisassociateAddress",
      "ec2:DescribeAddresses",
    ]
    resources = ["*"]
  }

  # VPC read-only access for subnet/VPC selection
  statement {
    sid = "VPCReadOnly"
    actions = [
      "ec2:DescribeVpcs",
      "ec2:DescribeSubnets",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeRouteTables",
      "ec2:DescribeInternetGateways",
    ]
    resources = ["*"]
  }

  # Tagging permissions
  statement {
    sid = "TaggingPermissions"
    actions = [
      "ec2:CreateTags",
      "ec2:DeleteTags",
      "ec2:DescribeTags",
    ]
    resources = ["*"]
  }

  # EBS volume management
  statement {
    sid = "EBSManagement"
    actions = [
      "ec2:CreateVolume",
      "ec2:DeleteVolume",
      "ec2:AttachVolume",
      "ec2:DetachVolume",
      "ec2:DescribeVolumes",
    ]
    resources = ["*"]
  }

  # Key pair management (optional, for SSH access)
  statement {
    sid = "KeyPairManagement"
    actions = [
      "ec2:CreateKeyPair",
      "ec2:DeleteKeyPair",
      "ec2:DescribeKeyPairs",
      "ec2:ImportKeyPair",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_user_policy" "caution_deploy" {
  name   = "CautionManagedOnPremAccess"
  user   = aws_iam_user.caution_deploy.name
  policy = data.aws_iam_policy_document.caution_deploy.json
}

resource "aws_iam_access_key" "caution_deploy" {
  user = aws_iam_user.caution_deploy.name
}

# Outputs
output "iam_user_name" {
  description = "Name of the IAM user for Caution deployments"
  value       = aws_iam_user.caution_deploy.name
}

output "iam_user_arn" {
  description = "ARN of the IAM user"
  value       = aws_iam_user.caution_deploy.arn
}

output "aws_access_key_id" {
  description = "AWS Access Key ID (upload to Caution platform)"
  value       = aws_iam_access_key.caution_deploy.id
  sensitive   = true
}

output "aws_secret_access_key" {
  description = "AWS Secret Access Key (upload to Caution platform)"
  value       = aws_iam_access_key.caution_deploy.secret
  sensitive   = true
}

output "aws_region" {
  description = "AWS region for deployments"
  value       = var.aws_region
}

output "aws_account_id" {
  description = "AWS Account ID"
  value       = data.aws_caller_identity.current.account_id
}
