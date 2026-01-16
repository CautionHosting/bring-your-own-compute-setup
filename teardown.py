"""
Tear down AWS infrastructure for a managed on-premises Caution deployment.

This script removes all resources created by setup.py:
1. Auto Scaling Group
2. Launch Template
3. IAM User and Policy
4. EC2 Instance Role and Profile
5. S3 Bucket (emptied first)
6. VPC and related resources (if created by setup)

Environment variables:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY - AWS credentials (required)
  AWS_REGION - AWS region (default: us-west-2)
  DEPLOYMENT_ID - The deployment ID to tear down (required)
"""

import json
import os
import sys
import time

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    print("Error: boto3 is required. Install with: pip install boto3")
    sys.exit(1)

# Tag key used for scoping all resources
SCOPE_TAG_KEY = "caution:deployment-id"


def delete_asg(asg_client, deployment_id):
    """Delete the Auto Scaling Group."""
    asg_name = f"caution-provisioner-asg-{deployment_id}"
    try:
        asg_client.delete_auto_scaling_group(
            AutoScalingGroupName=asg_name,
            ForceDelete=True,
        )
        print(f"  Deleted ASG: {asg_name}")
        return True
    except ClientError as e:
        if "AutoScalingGroup name not found" in str(e):
            print(f"  ASG not found: {asg_name}")
            return False
        raise


def delete_launch_template(ec2, deployment_id):
    """Delete the Launch Template."""
    template_name = f"caution-provisioner-lt-{deployment_id}"
    try:
        ec2.delete_launch_template(LaunchTemplateName=template_name)
        print(f"  Deleted Launch Template: {template_name}")
        return True
    except ClientError as e:
        if "does not exist" in str(e) or "InvalidLaunchTemplateName.NotFoundException" in str(e):
            print(f"  Launch Template not found: {template_name}")
            return False
        raise


def delete_iam_user(iam, account_id, deployment_id):
    """Delete the IAM user and associated policy."""
    user_name = f"caution-provisioner-{deployment_id}"
    policy_name = f"CautionScopedAccess-{deployment_id}"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

    # Detach and delete user
    try:
        # Detach managed policies
        try:
            attached = iam.list_attached_user_policies(UserName=user_name)
            for policy in attached["AttachedPolicies"]:
                iam.detach_user_policy(UserName=user_name, PolicyArn=policy["PolicyArn"])
        except ClientError:
            pass

        # Delete access keys
        try:
            keys = iam.list_access_keys(UserName=user_name)
            for key in keys["AccessKeyMetadata"]:
                iam.delete_access_key(UserName=user_name, AccessKeyId=key["AccessKeyId"])
        except ClientError:
            pass

        # Delete inline policies
        try:
            policies = iam.list_user_policies(UserName=user_name)
            for policy in policies["PolicyNames"]:
                iam.delete_user_policy(UserName=user_name, PolicyName=policy)
        except ClientError:
            pass

        iam.delete_user(UserName=user_name)
        print(f"  Deleted IAM User: {user_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  IAM User not found: {user_name}")

    # Delete managed policy
    try:
        iam.delete_policy(PolicyArn=policy_arn)
        print(f"  Deleted IAM Policy: {policy_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  IAM Policy not found: {policy_name}")


def delete_ec2_role(iam, deployment_id):
    """Delete the EC2 instance role and profile."""
    role_name = f"caution-ec2-role-{deployment_id}"
    profile_name = f"caution-ec2-profile-{deployment_id}"

    # Remove role from instance profile and delete profile
    try:
        try:
            iam.remove_role_from_instance_profile(
                InstanceProfileName=profile_name,
                RoleName=role_name,
            )
        except ClientError:
            pass
        iam.delete_instance_profile(InstanceProfileName=profile_name)
        print(f"  Deleted Instance Profile: {profile_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  Instance Profile not found: {profile_name}")

    # Delete role policies and role
    try:
        # Delete inline policies
        try:
            policies = iam.list_role_policies(RoleName=role_name)
            for policy in policies["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=policy)
        except ClientError:
            pass

        # Detach managed policies
        try:
            attached = iam.list_attached_role_policies(RoleName=role_name)
            for policy in attached["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
        except ClientError:
            pass

        iam.delete_role(RoleName=role_name)
        print(f"  Deleted IAM Role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  IAM Role not found: {role_name}")


def delete_s3_bucket(s3, deployment_id):
    """Empty and delete the S3 bucket."""
    bucket_name = f"caution-{deployment_id}-images"

    try:
        # Empty the bucket first
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            if "Contents" in page:
                objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                s3.delete_objects(Bucket=bucket_name, Delete={"Objects": objects})

        # Delete the bucket
        s3.delete_bucket(Bucket=bucket_name)
        print(f"  Deleted S3 Bucket: {bucket_name}")
        return True
    except ClientError as e:
        if "NoSuchBucket" in str(e):
            print(f"  S3 Bucket not found: {bucket_name}")
            return False
        raise


def delete_vpc_resources(ec2, deployment_id):
    """Delete VPC and related resources if they were created by setup."""
    # Find VPC by tag
    vpcs = ec2.describe_vpcs(
        Filters=[{"Name": f"tag:{SCOPE_TAG_KEY}", "Values": [deployment_id]}]
    )

    if not vpcs["Vpcs"]:
        print("  No VPC found with deployment tag (may be using existing VPC)")
        return False

    vpc_id = vpcs["Vpcs"][0]["VpcId"]
    print(f"  Found VPC: {vpc_id}")

    # Delete subnets
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    for subnet in subnets["Subnets"]:
        try:
            ec2.delete_subnet(SubnetId=subnet["SubnetId"])
            print(f"  Deleted Subnet: {subnet['SubnetId']}")
        except ClientError as e:
            print(f"  Warning: Could not delete subnet {subnet['SubnetId']}: {e}")

    # Detach and delete internet gateway
    igws = ec2.describe_internet_gateways(
        Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
    )
    for igw in igws["InternetGateways"]:
        igw_id = igw["InternetGatewayId"]
        try:
            ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
            ec2.delete_internet_gateway(InternetGatewayId=igw_id)
            print(f"  Deleted Internet Gateway: {igw_id}")
        except ClientError as e:
            print(f"  Warning: Could not delete IGW {igw_id}: {e}")

    # Delete route tables (except main)
    route_tables = ec2.describe_route_tables(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "association.main", "Values": ["false"]},
        ]
    )
    for rt in route_tables["RouteTables"]:
        rt_id = rt["RouteTableId"]
        try:
            # Disassociate first
            for assoc in rt.get("Associations", []):
                if not assoc.get("Main", False):
                    ec2.disassociate_route_table(AssociationId=assoc["RouteTableAssociationId"])
            ec2.delete_route_table(RouteTableId=rt_id)
            print(f"  Deleted Route Table: {rt_id}")
        except ClientError as e:
            print(f"  Warning: Could not delete route table {rt_id}: {e}")

    # Delete security groups (except default)
    sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    for sg in sgs["SecurityGroups"]:
        if sg["GroupName"] != "default":
            try:
                ec2.delete_security_group(GroupId=sg["GroupId"])
                print(f"  Deleted Security Group: {sg['GroupId']}")
            except ClientError as e:
                print(f"  Warning: Could not delete SG {sg['GroupId']}: {e}")

    # Delete VPC
    try:
        ec2.delete_vpc(VpcId=vpc_id)
        print(f"  Deleted VPC: {vpc_id}")
        return True
    except ClientError as e:
        print(f"  Warning: Could not delete VPC {vpc_id}: {e}")
        return False


def main():
    print("=" * 60)
    print("Caution Managed On-Prem Teardown")
    print("=" * 60)
    print()

    # Get deployment ID
    deployment_id = os.environ.get("DEPLOYMENT_ID")
    if not deployment_id:
        print("[ERROR] DEPLOYMENT_ID environment variable is required")
        print()
        print("Set DEPLOYMENT_ID in your .env file to the deployment you want to tear down.")
        print("You can find this in the credentials.json file from the original setup.")
        sys.exit(1)

    # Check AWS credentials
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        account_id = identity["Account"]
        print(f"[OK] AWS Account: {account_id}")
    except NoCredentialsError:
        print("[ERROR] AWS credentials not configured.")
        print("Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in your .env file.")
        sys.exit(1)
    except ClientError as e:
        print(f"[ERROR] AWS error: {e}")
        sys.exit(1)

    # Get region
    session = boto3.session.Session()
    region = session.region_name or os.environ.get("AWS_REGION", "us-west-2")
    print(f"[OK] Region: {region}")
    print(f"[OK] Deployment ID: {deployment_id}")
    print()

    # Confirm teardown
    print("WARNING: This will permanently delete all resources for this deployment!")
    print("         This action cannot be undone.")
    print()

    # Initialize clients
    ec2 = boto3.client("ec2", region_name=region)
    asg_client = boto3.client("autoscaling", region_name=region)
    iam = boto3.client("iam")
    s3 = boto3.client("s3", region_name=region)

    # Delete resources in reverse order of creation
    print("[1/6] Deleting Auto Scaling Group...")
    delete_asg(asg_client, deployment_id)

    # Wait a bit for ASG to clean up instances
    print("  Waiting for ASG cleanup...")
    time.sleep(5)

    print()
    print("[2/6] Deleting Launch Template...")
    delete_launch_template(ec2, deployment_id)

    print()
    print("[3/6] Deleting IAM User and Policy...")
    delete_iam_user(iam, account_id, deployment_id)

    print()
    print("[4/6] Deleting EC2 Instance Role and Profile...")
    delete_ec2_role(iam, deployment_id)

    print()
    print("[5/6] Deleting S3 Bucket...")
    delete_s3_bucket(s3, deployment_id)

    print()
    print("[6/6] Deleting VPC Resources...")
    delete_vpc_resources(ec2, deployment_id)

    print()
    print("=" * 60)
    print("Teardown Complete")
    print("=" * 60)
    print()
    print(f"All resources for deployment {deployment_id} have been removed.")
    print()


if __name__ == "__main__":
    main()
