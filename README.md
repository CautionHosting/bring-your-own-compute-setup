# Managed On-Prem Setup

Create AWS IAM credentials for [Caution](https://caution.dev) managed on-premises deployments.

This creates an IAM user with the minimum permissions required to deploy enclaves to your AWS account.

## Setup Options

Choose your preferred method:

| Method | Directory | Prerequisites |
|--------|-----------|---------------|
| [Terraform/OpenTofu](aws/tf/) | `aws/tf/` | Terraform or OpenTofu |
| [AWS SDK (Python)](aws/sdk/) | `aws/sdk/` | Python 3, boto3 |

## Quick Start

### Option 1: Terraform

```bash
cd aws/tf
./setup.sh
```

### Option 2: Python SDK

```bash
cd aws/sdk
pip install boto3
python setup.py
```

## Permissions Created

The IAM user has permissions for:
- **EC2**: Create/manage instances, security groups, EIPs
- **VPC**: Read-only access for subnet selection
- **EBS**: Volume management
- **Tags**: Resource tagging

## Restricting Permissions

The default policy is permissive for ease of setup. You can modify the scripts to create a more restrictive policy:

### Limit to specific regions

Add a condition to restrict deployments to specific AWS regions:

```json
"Condition": {
  "StringEquals": {
    "aws:RequestedRegion": ["us-west-2", "us-east-1"]
  }
}
```

### Limit to specific VPCs

Restrict instance creation to specific VPCs:

```json
"Condition": {
  "StringEquals": {
    "ec2:Vpc": "arn:aws:ec2:us-west-2:123456789012:vpc/vpc-abc123"
  }
}
```

### Limit to specific subnets

Restrict to specific subnets within a VPC:

```json
"Condition": {
  "StringEquals": {
    "ec2:Subnet": [
      "arn:aws:ec2:us-west-2:123456789012:subnet/subnet-abc123",
      "arn:aws:ec2:us-west-2:123456789012:subnet/subnet-def456"
    ]
  }
}
```

### Limit instance types

Restrict to specific instance types (e.g., only Nitro enclave-capable):

```json
"Condition": {
  "StringEquals": {
    "ec2:InstanceType": ["c5.xlarge", "c5.2xlarge", "m5.xlarge"]
  }
}
```

Edit `aws/tf/main.tf` or `aws/sdk/setup.py` to add these conditions to the policy statements.

## Adding to Caution

1. Run setup using either method above
2. Copy the Access Key ID and Secret Access Key
3. Go to your Caution dashboard → Cloud Credentials
4. Add the credentials
5. Add the `managed_on_prem: true` value in your `Procfile`
  * Optional values such as `region`, `vpc` etc. are available (TODO link to docs)
6. Deploy with: `caution init && git push caution main`
