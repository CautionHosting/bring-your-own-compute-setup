# Terraform Setup

Create AWS IAM credentials using Terraform/OpenTofu.

## Prerequisites

- AWS CLI configured with admin credentials
- OpenTofu or Terraform installed

## Usage

```bash
./setup.sh
```

## Manual Steps

```bash
tofu init
tofu plan
tofu apply
```

## Cleanup

```bash
tofu destroy
```
