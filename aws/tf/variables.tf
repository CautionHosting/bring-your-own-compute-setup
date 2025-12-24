# SPDX-FileCopyrightText: 2025 Caution SEZC
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial

variable "aws_region" {
  description = "AWS region for deployments"
  type        = string
  default     = "us-west-2"
}

variable "iam_user_name" {
  description = "Name of the IAM user for Caution deployments"
  type        = string
  default     = "caution-managed-onprem"
}
