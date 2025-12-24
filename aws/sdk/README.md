# AWS SDK Setup

Create AWS IAM credentials using Python and boto3.

## Prerequisites

- Python 3
- boto3: `pip install boto3`
- AWS CLI configured with admin credentials

## Usage

```bash
python setup.py
```

## Cleanup

To delete the IAM user:

```bash
aws iam delete-access-key --user-name caution-managed-onprem --access-key-id <KEY_ID>
aws iam delete-user-policy --user-name caution-managed-onprem --policy-name CautionManagedOnPremAccess
aws iam delete-user --user-name caution-managed-onprem
```
