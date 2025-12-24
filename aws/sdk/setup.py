#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Caution SEZC
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial

"""
Create AWS IAM credentials for Caution managed on-prem deployments.
"""

import json
import sys

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    print("Error: boto3 is required. Install with: pip install boto3")
    sys.exit(1)

# IAM policy for Caution deployments
POLICY_DOCUMENT = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "EC2InstanceManagement",
            "Effect": "Allow",
            "Action": [
                "ec2:RunInstances",
                "ec2:TerminateInstances",
                "ec2:DescribeInstances",
                "ec2:DescribeInstanceStatus",
                "ec2:StartInstances",
                "ec2:StopInstances",
                "ec2:RebootInstances",
                "ec2:DescribeImages",
                "ec2:DescribeInstanceTypes"
            ],
            "Resource": "*"
        },
        {
            "Sid": "SecurityGroupManagement",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateSecurityGroup",
                "ec2:DeleteSecurityGroup",
                "ec2:DescribeSecurityGroups",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:AuthorizeSecurityGroupEgress",
                "ec2:RevokeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupEgress"
            ],
            "Resource": "*"
        },
        {
            "Sid": "ElasticIPManagement",
            "Effect": "Allow",
            "Action": [
                "ec2:AllocateAddress",
                "ec2:ReleaseAddress",
                "ec2:AssociateAddress",
                "ec2:DisassociateAddress",
                "ec2:DescribeAddresses"
            ],
            "Resource": "*"
        },
        {
            "Sid": "VPCReadOnly",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeVpcs",
                "ec2:DescribeSubnets",
                "ec2:DescribeAvailabilityZones",
                "ec2:DescribeRouteTables",
                "ec2:DescribeInternetGateways"
            ],
            "Resource": "*"
        },
        {
            "Sid": "TaggingPermissions",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateTags",
                "ec2:DeleteTags",
                "ec2:DescribeTags"
            ],
            "Resource": "*"
        },
        {
            "Sid": "EBSManagement",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateVolume",
                "ec2:DeleteVolume",
                "ec2:AttachVolume",
                "ec2:DetachVolume",
                "ec2:DescribeVolumes"
            ],
            "Resource": "*"
        },
        {
            "Sid": "KeyPairManagement",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateKeyPair",
                "ec2:DeleteKeyPair",
                "ec2:DescribeKeyPairs",
                "ec2:ImportKeyPair"
            ],
            "Resource": "*"
        }
    ]
}

USER_NAME = "caution-managed-onprem"
POLICY_NAME = "CautionManagedOnPremAccess"


def main():
    print("Caution Managed On-Prem Setup (AWS SDK)")
    print("=" * 40)
    print()

    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        account_id = identity["Account"]
        print(f"✓ AWS Account: {account_id}")
    except NoCredentialsError:
        print("❌ AWS credentials not configured.")
        print("Please run: aws configure")
        sys.exit(1)
    except ClientError as e:
        print(f"❌ AWS error: {e}")
        sys.exit(1)

    iam = boto3.client("iam")

    # Check if user already exists
    try:
        iam.get_user(UserName=USER_NAME)
        print(f"⚠ User '{USER_NAME}' already exists.")
        response = input("Delete and recreate? (yes/no): ")
        if response.lower() != "yes":
            print("Aborted.")
            sys.exit(0)

        # Delete existing access keys
        keys = iam.list_access_keys(UserName=USER_NAME)
        for key in keys["AccessKeyMetadata"]:
            iam.delete_access_key(UserName=USER_NAME, AccessKeyId=key["AccessKeyId"])

        # Delete inline policies
        policies = iam.list_user_policies(UserName=USER_NAME)
        for policy in policies["PolicyNames"]:
            iam.delete_user_policy(UserName=USER_NAME, PolicyName=policy)

        # Delete user
        iam.delete_user(UserName=USER_NAME)
        print(f"✓ Deleted existing user '{USER_NAME}'")
    except iam.exceptions.NoSuchEntityException:
        pass

    # Create user
    print(f"Creating IAM user '{USER_NAME}'...")
    iam.create_user(
        UserName=USER_NAME,
        Tags=[
            {"Key": "Purpose", "Value": "caution-managed-onprem"},
            {"Key": "ManagedBy", "Value": "managed-onprem-setup"}
        ]
    )
    print(f"✓ Created user '{USER_NAME}'")

    # Attach policy
    print("Attaching IAM policy...")
    iam.put_user_policy(
        UserName=USER_NAME,
        PolicyName=POLICY_NAME,
        PolicyDocument=json.dumps(POLICY_DOCUMENT)
    )
    print(f"✓ Attached policy '{POLICY_NAME}'")

    # Create access key
    print("Creating access key...")
    key_response = iam.create_access_key(UserName=USER_NAME)
    access_key = key_response["AccessKey"]["AccessKeyId"]
    secret_key = key_response["AccessKey"]["SecretAccessKey"]
    print("✓ Created access key")

    # Get region
    session = boto3.session.Session()
    region = session.region_name or "us-west-2"

    print()
    print("=" * 40)
    print("Credentials for Caution Platform")
    print("=" * 40)
    print()
    print("Add these to your Caution dashboard under Cloud Credentials:")
    print()
    print(f"  Access Key ID:     {access_key}")
    print(f"  Secret Access Key: {secret_key}")
    print(f"  Region:            {region}")
    print()
    print("=" * 40)

    # Save to file
    creds_file = "caution-credentials.env"
    with open(creds_file, "w") as f:
        f.write(f"AWS_ACCESS_KEY_ID={access_key}\n")
        f.write(f"AWS_SECRET_ACCESS_KEY={secret_key}\n")
        f.write(f"AWS_REGION={region}\n")

    import os
    os.chmod(creds_file, 0o600)

    print()
    print(f"✓ Credentials saved to: {creds_file}")
    print("⚠ Keep this file secure!")
    print()
    print("✅ Setup complete!")


if __name__ == "__main__":
    main()
