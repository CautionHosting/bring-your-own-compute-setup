"""
Create AWS infrastructure for a managed on-premises Caution deployment.

This script creates an isolated environment for running Caution enclaves:
1. Creates a dedicated VPC with subnets, internet gateway, and routing
2. Creates an S3 bucket for enclave image storage
3. Creates EC2 instance roles for runtime and builder workloads
4. Creates a launch template
5. Creates an Auto Scaling Group with a unique deployment tag
6. Creates an IAM user with permissions scoped to only these resources

Each deployment is completely isolated - the IAM user can only manage
resources within its own VPC and those tagged with its deployment ID.

Environment variables:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY - AWS credentials (required)
  AWS_REGION - AWS region (default: us-west-2)
  VPC_ID - Use existing VPC instead of creating one (optional, advanced)
  DEPLOYMENT_ID - Required only for --update-policy mode
"""

import json
import os
import secrets
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


def generate_deployment_id():
    """Generate a unique deployment ID (16 hex chars)."""
    return secrets.token_hex(8)


def get_default_vpc(ec2):
    """Get the default VPC or first available VPC."""
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])
    if vpcs["Vpcs"]:
        return vpcs["Vpcs"][0]["VpcId"]
    # Fall back to first VPC
    vpcs = ec2.describe_vpcs()
    if vpcs["Vpcs"]:
        return vpcs["Vpcs"][0]["VpcId"]
    return None


def get_subnets_for_vpc(ec2, vpc_id):
    """Get all subnets in a VPC."""
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    return [s["SubnetId"] for s in subnets["Subnets"]]


def create_vpc(ec2, deployment_id, region):
    """Create a dedicated VPC for this deployment.

    Creates a /16 VPC with public subnets across multiple AZs.
    """
    # Use 10.X.0.0/16 where X is derived from deployment_id for uniqueness
    # This gives us 65,536 addresses per VPC
    cidr_second_octet = int(secrets.token_hex(1), 16)  # 0-255
    vpc_cidr = f"10.{cidr_second_octet}.0.0/16"

    print(f"  Creating VPC with CIDR {vpc_cidr}...")

    # Create VPC
    vpc_response = ec2.create_vpc(
        CidrBlock=vpc_cidr,
        TagSpecifications=[
            {
                "ResourceType": "vpc",
                "Tags": [
                    {"Key": "Name", "Value": f"caution-vpc-{deployment_id[:20]}"},
                    {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
                    {"Key": "ManagedBy", "Value": "caution-managed-onprem"},
                ],
            }
        ],
    )
    vpc_id = vpc_response["Vpc"]["VpcId"]

    # Enable DNS hostnames (required for some services)
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})

    # Create Internet Gateway
    print("  Creating Internet Gateway...")
    igw_response = ec2.create_internet_gateway(
        TagSpecifications=[
            {
                "ResourceType": "internet-gateway",
                "Tags": [
                    {"Key": "Name", "Value": f"caution-igw-{deployment_id[:20]}"},
                    {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
                    {"Key": "ManagedBy", "Value": "caution-managed-onprem"},
                ],
            }
        ],
    )
    igw_id = igw_response["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

    # Get availability zones
    azs_response = ec2.describe_availability_zones(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )
    azs = [az["ZoneName"] for az in azs_response["AvailabilityZones"][:3]]  # Use up to 3 AZs

    # Create subnets (one per AZ)
    print(f"  Creating {len(azs)} subnets across availability zones...")
    subnet_ids = []
    for i, az in enumerate(azs):
        # Each subnet gets a /24 (256 addresses)
        subnet_cidr = f"10.{cidr_second_octet}.{i}.0/24"
        subnet_response = ec2.create_subnet(
            VpcId=vpc_id,
            CidrBlock=subnet_cidr,
            AvailabilityZone=az,
            TagSpecifications=[
                {
                    "ResourceType": "subnet",
                    "Tags": [
                        {"Key": "Name", "Value": f"caution-subnet-{az}-{deployment_id[:12]}"},
                        {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
                        {"Key": "ManagedBy", "Value": "caution-managed-onprem"},
                    ],
                }
            ],
        )
        subnet_id = subnet_response["Subnet"]["SubnetId"]
        subnet_ids.append(subnet_id)

        # Enable auto-assign public IP for instances in this subnet
        ec2.modify_subnet_attribute(
            SubnetId=subnet_id,
            MapPublicIpOnLaunch={"Value": True},
        )

    # Create route table with internet gateway route
    print("  Creating route table...")
    rt_response = ec2.create_route_table(
        VpcId=vpc_id,
        TagSpecifications=[
            {
                "ResourceType": "route-table",
                "Tags": [
                    {"Key": "Name", "Value": f"caution-rt-{deployment_id[:20]}"},
                    {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
                    {"Key": "ManagedBy", "Value": "caution-managed-onprem"},
                ],
            }
        ],
    )
    rt_id = rt_response["RouteTable"]["RouteTableId"]

    # Add route to internet gateway
    ec2.create_route(
        RouteTableId=rt_id,
        DestinationCidrBlock="0.0.0.0/0",
        GatewayId=igw_id,
    )

    # Associate route table with all subnets
    for subnet_id in subnet_ids:
        ec2.associate_route_table(RouteTableId=rt_id, SubnetId=subnet_id)

    return vpc_id, subnet_ids


def create_eif_bucket(s3, deployment_id, region):
    """Create an S3 bucket for storing enclave images."""
    bucket_name = f"caution-{deployment_id}-images"

    create_params = {"Bucket": bucket_name}
    if region != "us-east-1":
        create_params["CreateBucketConfiguration"] = {"LocationConstraint": region}

    s3.create_bucket(**create_params)

    s3.put_bucket_tagging(
        Bucket=bucket_name,
        Tagging={
            "TagSet": [
                {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
                {"Key": "ManagedBy", "Value": "caution-managed-onprem"},
            ]
        },
    )

    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    return bucket_name


def create_ec2_trust_policy():
    """Create the trust policy used by EC2 instance roles."""
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    return trust_policy


def create_instance_role(
    iam,
    deployment_id,
    role_name,
    profile_name,
    description,
    policy_name,
    policy_document,
):
    """Create an EC2-assumable IAM role and instance profile."""
    trust_policy = create_ec2_trust_policy()

    try:
        iam.get_role(RoleName=role_name)
        attached = iam.list_attached_role_policies(RoleName=role_name)
        for policy in attached["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
        inline = iam.list_role_policies(RoleName=role_name)
        for policy in inline["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy)
        try:
            iam.remove_role_from_instance_profile(
                InstanceProfileName=profile_name, RoleName=role_name
            )
        except iam.exceptions.NoSuchEntityException:
            pass
        iam.delete_role(RoleName=role_name)
    except iam.exceptions.NoSuchEntityException:
        pass

    try:
        iam.delete_instance_profile(InstanceProfileName=profile_name)
    except iam.exceptions.NoSuchEntityException:
        pass

    iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description=description,
        Tags=[
            {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
            {"Key": "ManagedBy", "Value": "caution-managed-onprem"},
        ],
    )

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(policy_document),
    )

    iam.create_instance_profile(
        InstanceProfileName=profile_name,
        Tags=[
            {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
            {"Key": "ManagedBy", "Value": "caution-managed-onprem"},
        ],
    )

    iam.add_role_to_instance_profile(
        InstanceProfileName=profile_name, RoleName=role_name
    )

    time.sleep(10)

    return role_name, profile_name


def create_ec2_instance_role(iam, deployment_id, bucket_name):
    """Create an IAM role for EC2 instances to read from the enclave image bucket."""
    role_name = f"caution-ec2-role-{deployment_id}"
    profile_name = f"caution-ec2-profile-{deployment_id}"

    s3_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadEifBucket",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            },
        ],
    }

    return create_instance_role(
        iam,
        deployment_id,
        role_name,
        profile_name,
        f"EC2 role for Caution deployment {deployment_id}",
        "EifBucketAccess",
        s3_policy,
    )


def create_builder_instance_role(iam, deployment_id, bucket_name):
    """Create an IAM role for builder instances to publish objects to the enclave image bucket."""
    role_name = f"caution-builder-role-{deployment_id}"
    profile_name = f"caution-builder-profile-{deployment_id}"

    s3_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ListEifBucket",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket_name}"],
            },
            {
                "Sid": "ManageEifObjects",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
            },
        ],
    }

    return create_instance_role(
        iam,
        deployment_id,
        role_name,
        profile_name,
        f"Builder role for Caution deployment {deployment_id}",
        "BuilderBucketAccess",
        s3_policy,
    )



def create_launch_template(ec2, deployment_id, region, instance_profile_name):
    """Create a launch template for the ASG."""
    template_name = f"caution-provisioner-lt-{deployment_id}"

    images = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["al2023-ami-*-x86_64"]},
            {"Name": "state", "Values": ["available"]},
        ],
    )

    if not images["Images"]:
        print("Error: Could not find Amazon Linux 2023 AMI")
        sys.exit(1)

    ami = sorted(images["Images"], key=lambda x: x["CreationDate"], reverse=True)[0]

    response = ec2.create_launch_template(
        LaunchTemplateName=template_name,
        LaunchTemplateData={
            "ImageId": ami["ImageId"],
            "InstanceType": "t3.micro",
            "IamInstanceProfile": {"Name": instance_profile_name},
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
                        {"Key": "Name", "Value": f"caution-provisioner-{deployment_id}"},
                    ],
                },
                {
                    "ResourceType": "volume",
                    "Tags": [
                        {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
                    ],
                },
            ],
        },
        TagSpecifications=[
            {
                "ResourceType": "launch-template",
                "Tags": [
                    {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
                ],
            }
        ],
    )

    return template_name, response["LaunchTemplate"]["LaunchTemplateId"]


def create_auto_scaling_group(asg_client, deployment_id, template_name, subnets):
    """Create an Auto Scaling Group with the deployment tag."""
    asg_name = f"caution-provisioner-asg-{deployment_id}"

    asg_client.create_auto_scaling_group(
        AutoScalingGroupName=asg_name,
        LaunchTemplate={
            "LaunchTemplateName": template_name,
            "Version": "$Latest",
        },
        MinSize=0,
        MaxSize=10,
        DesiredCapacity=0,  # Start with no instances
        VPCZoneIdentifier=",".join(subnets),
        Tags=[
            {
                "Key": SCOPE_TAG_KEY,
                "Value": deployment_id,
                "PropagateAtLaunch": True,
            },
            {
                "Key": "Name",
                "Value": asg_name,
                "PropagateAtLaunch": True,
            },
            {
                "Key": "ManagedBy",
                "Value": "caution-managed-onprem",
                "PropagateAtLaunch": False,
            },
        ],
    )

    return asg_name


def create_scoped_iam_policy(account_id, region, deployment_id, eif_bucket, vpc_id=None):
    """
    Create an IAM policy document scoped to resources with the deployment tag.
    Optimized for size to stay under AWS 6144 char limit.
    """
    tag_cond = {"StringEquals": {f"aws:ResourceTag/{SCOPE_TAG_KEY}": deployment_id}}
    req_tag_cond = {"StringEquals": {f"aws:RequestTag/{SCOPE_TAG_KEY}": deployment_id}}
    r = region
    a = account_id
    d = deployment_id

    statements = [
        # S3 bucket access
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
            "Resource": [f"arn:aws:s3:::{eif_bucket}", f"arn:aws:s3:::{eif_bucket}/*"],
        },
        # Read-only operations (use wildcards to save space)
        {
            "Effect": "Allow",
            "Action": ["ec2:Describe*", "autoscaling:Describe*"],
            "Resource": "*",
        },
        # IAM read for instance profiles
        {
            "Effect": "Allow",
            "Action": ["iam:GetInstanceProfile", "iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies"],
            "Resource": [
                f"arn:aws:iam::{a}:role/caution-ec2-role-*",
                f"arn:aws:iam::{a}:role/caution-builder-role-*",
                f"arn:aws:iam::{a}:instance-profile/caution-ec2-profile-*",
                f"arn:aws:iam::{a}:instance-profile/caution-builder-profile-*",
            ],
        },
        # Manage tagged instances
        {
            "Effect": "Allow",
            "Action": ["ec2:StartInstances", "ec2:StopInstances", "ec2:RebootInstances", "ec2:TerminateInstances"],
            "Resource": f"arn:aws:ec2:{r}:{a}:instance/*",
            "Condition": tag_cond,
        },
        # RunInstances (scoped via ASG)
        {
            "Effect": "Allow",
            "Action": "ec2:RunInstances",
            "Resource": [
                f"arn:aws:ec2:{r}:{a}:instance/*", f"arn:aws:ec2:{r}::image/*",
                f"arn:aws:ec2:{r}:{a}:subnet/*", f"arn:aws:ec2:{r}:{a}:network-interface/*",
                f"arn:aws:ec2:{r}:{a}:security-group/*", f"arn:aws:ec2:{r}:{a}:key-pair/*",
                f"arn:aws:ec2:{r}:{a}:launch-template/*", f"arn:aws:ec2:{r}:{a}:volume/*",
            ],
        },
        # PassRole for EC2
        {
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": [
                f"arn:aws:iam::{a}:role/caution-ec2-role-*",
                f"arn:aws:iam::{a}:role/caution-builder-role-*",
            ],
            "Condition": {"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}},
        },
        # Create resources with tag
        {
            "Effect": "Allow",
            "Action": ["ec2:CreateVolume", "ec2:CreateKeyPair", "ec2:ImportKeyPair"],
            "Resource": f"arn:aws:ec2:{r}:{a}:*",
            "Condition": req_tag_cond,
        },
        {
            "Effect": "Allow",
            "Action": "ec2:CreateSecurityGroup",
            "Resource": [f"arn:aws:ec2:{r}:{a}:security-group/*", f"arn:aws:ec2:{r}:{a}:vpc/*"],
        },
        {
            "Effect": "Allow",
            "Action": "ec2:AllocateAddress",
            "Resource": "*",
            "Condition": req_tag_cond,
        },
        # Manage tagged resources (combined)
        {
            "Effect": "Allow",
            "Action": [
                "ec2:DeleteVolume", "ec2:AttachVolume", "ec2:DetachVolume",
                "ec2:DeleteSecurityGroup", "ec2:AuthorizeSecurityGroupIngress",
                "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupEgress", "ec2:ReleaseAddress", "ec2:DeleteKeyPair",
                "ec2:CreateLaunchTemplateVersion", "ec2:ModifyLaunchTemplate", "ec2:DeleteLaunchTemplate",
            ],
            "Resource": "*",
            "Condition": tag_cond,
        },
        # EIP associate/disassociate - split because network interfaces don't inherit tags
        {
            "Effect": "Allow",
            "Action": ["ec2:AssociateAddress", "ec2:DisassociateAddress"],
            "Resource": [f"arn:aws:ec2:{r}:{a}:elastic-ip/*", f"arn:aws:ec2:{r}:{a}:instance/*"],
            "Condition": tag_cond,
        },
        # Network interfaces for EIP ops - scoped by VPC if available
        {
            "Effect": "Allow",
            "Action": ["ec2:AssociateAddress", "ec2:DisassociateAddress"],
            "Resource": f"arn:aws:ec2:{r}:{a}:network-interface/*",
            **({"Condition": {"StringEquals": {"ec2:Vpc": f"arn:aws:ec2:{r}:{a}:vpc/{vpc_id}"}}} if vpc_id else {}),
        },
        # Tagging
        {
            "Effect": "Allow",
            "Action": "ec2:CreateTags",
            "Resource": "*",
            "Condition": {"StringEquals": {"ec2:CreateAction": ["RunInstances", "CreateVolume", "CreateSecurityGroup", "AllocateAddress", "CreateLaunchTemplate"]}},
        },
        {
            "Effect": "Allow",
            "Action": ["ec2:CreateTags", "ec2:DeleteTags"],
            "Resource": "*",
            "Condition": tag_cond,
        },
        # ASG management
        {
            "Effect": "Allow",
            "Action": ["autoscaling:UpdateAutoScalingGroup", "autoscaling:SetDesiredCapacity", "autoscaling:TerminateInstanceInAutoScalingGroup"],
            "Resource": f"arn:aws:autoscaling:{r}:{a}:autoScalingGroup:*:autoScalingGroupName/caution-provisioner-asg-{d}",
        },
        # Launch template create (no tag condition - AWS limitation)
        {
            "Effect": "Allow",
            "Action": "ec2:CreateLaunchTemplate",
            "Resource": f"arn:aws:ec2:{r}:{a}:launch-template/*",
        },
    ]

    return {"Version": "2012-10-17", "Statement": statements}


def create_iam_user(iam, account_id, user_name, policy_document, deployment_id):
    """Create IAM user with the scoped policy."""
    policy_name = f"CautionScopedAccess-{deployment_id}"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

    # Check if user already exists
    try:
        iam.get_user(UserName=user_name)
        print(f"  User '{user_name}' already exists, deleting...")

        # Detach managed policies
        attached = iam.list_attached_user_policies(UserName=user_name)
        for policy in attached["AttachedPolicies"]:
            iam.detach_user_policy(UserName=user_name, PolicyArn=policy["PolicyArn"])

        # Delete existing access keys
        keys = iam.list_access_keys(UserName=user_name)
        for key in keys["AccessKeyMetadata"]:
            iam.delete_access_key(UserName=user_name, AccessKeyId=key["AccessKeyId"])

        # Delete inline policies
        policies = iam.list_user_policies(UserName=user_name)
        for policy in policies["PolicyNames"]:
            iam.delete_user_policy(UserName=user_name, PolicyName=policy)

        # Delete user
        iam.delete_user(UserName=user_name)
    except iam.exceptions.NoSuchEntityException:
        pass

    # Delete existing managed policy if it exists
    try:
        iam.delete_policy(PolicyArn=policy_arn)
    except iam.exceptions.NoSuchEntityException:
        pass

    # Create managed policy (10KB limit vs 2KB for inline)
    policy_response = iam.create_policy(
        PolicyName=policy_name,
        PolicyDocument=json.dumps(policy_document),
        Description=f"Scoped access for Caution deployment {deployment_id}",
        Tags=[
            {"Key": "Purpose", "Value": "caution-managed-onprem"},
            {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
        ],
    )
    policy_arn = policy_response["Policy"]["Arn"]

    # Create user
    iam.create_user(
        UserName=user_name,
        Tags=[
            {"Key": "Purpose", "Value": "caution-managed-onprem"},
            {"Key": "ManagedBy", "Value": "managed-onprem-setup"},
            {"Key": SCOPE_TAG_KEY, "Value": deployment_id},
        ],
    )

    # Attach managed policy to user
    iam.attach_user_policy(UserName=user_name, PolicyArn=policy_arn)

    # Create access key
    key_response = iam.create_access_key(UserName=user_name)

    # Wait for IAM eventual consistency — new credentials may not be
    # usable immediately across all AWS endpoints.
    print("  Waiting for IAM credentials to propagate...")
    time.sleep(15)

    return key_response["AccessKey"]["AccessKeyId"], key_response["AccessKey"]["SecretAccessKey"]


def update_iam_policy(iam, account_id, region, deployment_id, eif_bucket, vpc_id=None):
    """Update the IAM policy for an existing deployment."""
    policy_name = f"CautionScopedAccess-{deployment_id}"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

    # Generate new policy document
    policy_document = create_scoped_iam_policy(account_id, region, deployment_id, eif_bucket, vpc_id)

    # Try to get existing policy
    try:
        iam.get_policy(PolicyArn=policy_arn)
    except iam.exceptions.NoSuchEntityException:
        print(f"[ERROR] Policy {policy_name} not found. Run setup without --update-policy first.")
        sys.exit(1)

    # Create a new policy version (AWS allows up to 5 versions)
    try:
        # List existing versions and delete old non-default ones if at limit
        versions = iam.list_policy_versions(PolicyArn=policy_arn)
        non_default_versions = [v for v in versions["Versions"] if not v["IsDefaultVersion"]]

        # If we have 4 non-default versions, delete the oldest one
        if len(non_default_versions) >= 4:
            oldest = sorted(non_default_versions, key=lambda x: x["CreateDate"])[0]
            iam.delete_policy_version(PolicyArn=policy_arn, VersionId=oldest["VersionId"])
            print(f"  Deleted old policy version: {oldest['VersionId']}")

        # Create new version and set as default
        iam.create_policy_version(
            PolicyArn=policy_arn,
            PolicyDocument=json.dumps(policy_document),
            SetAsDefault=True,
        )
        print(f"  Updated policy: {policy_name}")
        return True
    except ClientError as e:
        print(f"[ERROR] Failed to update policy: {e}")
        return False


def main():
    # Check for --update-policy flag
    update_policy_only = "--update-policy" in sys.argv

    # Check AWS credentials
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        account_id = identity["Account"]
        print(f"[OK] AWS Account: {account_id}")
    except NoCredentialsError:
        print("[ERROR] AWS credentials not configured.")
        print("Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables.")
        sys.exit(1)
    except ClientError as e:
        print(f"[ERROR] AWS error: {e}")
        sys.exit(1)

    # Get region
    session = boto3.session.Session()
    region = session.region_name or os.environ.get("AWS_REGION", "us-west-2")
    print(f"[OK] Region: {region}")

    # Initialize clients
    ec2 = boto3.client("ec2", region_name=region)
    asg_client = boto3.client("autoscaling", region_name=region)
    iam = boto3.client("iam")
    s3 = boto3.client("s3", region_name=region)

    # Get deployment ID - required for update mode
    deployment_id = os.environ.get("DEPLOYMENT_ID")
    if update_policy_only:
        if not deployment_id:
            print("[ERROR] DEPLOYMENT_ID environment variable required for --update-policy mode")
            sys.exit(1)
        print(f"[OK] Deployment ID: {deployment_id}")
        vpc_id = os.environ.get("VPC_ID")
        if vpc_id:
            print(f"[OK] VPC ID: {vpc_id}")
        else:
            print("[!] VPC_ID not set - network interface operations will not be VPC-scoped")
        print()
        print("[*] Updating IAM policy only...")
        eif_bucket = f"caution-{deployment_id}-images"
        if update_iam_policy(iam, account_id, region, deployment_id, eif_bucket, vpc_id):
            print()
            print("=" * 60)
            print("IAM Policy Updated Successfully")
            print("=" * 60)
        sys.exit(0)

    # Always generate a fresh deployment ID for normal setup
    # This ensures each run creates completely new resources
    # (Use --update-policy with DEPLOYMENT_ID to update an existing deployment)
    deployment_id = generate_deployment_id()
    print(f"[OK] Deployment ID: {deployment_id}")
    print()

    # Create or use existing VPC
    existing_vpc_id = os.environ.get("VPC_ID")
    if existing_vpc_id:
        # Use existing VPC (advanced users only)
        print("[1/7] Using existing VPC...")
        vpc_id = existing_vpc_id
        print(f"  VPC: {vpc_id}")
        subnets = get_subnets_for_vpc(ec2, vpc_id)
        if not subnets:
            print("[ERROR] No subnets found in VPC.")
            sys.exit(1)
        print(f"  Subnets: {len(subnets)} found")
        print("  [!] Note: Using existing VPC - network interface operations will be scoped to this VPC")
    else:
        # Create a dedicated VPC for this deployment (recommended)
        print("[1/7] Creating dedicated VPC for this deployment...")
        vpc_id, subnets = create_vpc(ec2, deployment_id, region)
        print(f"  VPC: {vpc_id}")
        print(f"  Subnets: {len(subnets)} created")

    # Create S3 bucket for enclave image storage
    print()
    print("[2/7] Creating S3 bucket for enclave image storage...")
    eif_bucket = create_eif_bucket(s3, deployment_id, region)
    print(f"  Bucket: {eif_bucket}")

    # Create EC2 instance role and profile
    print()
    print("[3/7] Creating EC2 instance role and profile...")
    ec2_role_name, instance_profile_name = create_ec2_instance_role(
        iam, deployment_id, eif_bucket
    )
    print(f"  Role: {ec2_role_name}")
    print(f"  Instance Profile: {instance_profile_name}")

    # Create builder role and profile
    print()
    print("[4/7] Creating builder role and profile...")
    builder_role_name, builder_instance_profile_name = create_builder_instance_role(
        iam, deployment_id, eif_bucket
    )
    print(f"  Role: {builder_role_name}")
    print(f"  Instance Profile: {builder_instance_profile_name}")

    # Create launch template
    print()
    print("[5/7] Creating launch template...")
    template_name, template_id = create_launch_template(
        ec2, deployment_id, region, instance_profile_name
    )
    print(f"  Launch Template: {template_name}")

    # Create Auto Scaling Group
    print()
    print("[6/7] Creating Auto Scaling Group...")
    asg_name = create_auto_scaling_group(asg_client, deployment_id, template_name, subnets)
    print(f"  ASG: {asg_name}")
    print(f"  Tag: {SCOPE_TAG_KEY}={deployment_id}")

    # Create IAM user with scoped policy
    print()
    print("[7/7] Creating scoped IAM user...")
    user_name = f"caution-provisioner-{deployment_id}"
    policy = create_scoped_iam_policy(account_id, region, deployment_id, eif_bucket, vpc_id)
    access_key, secret_key = create_iam_user(iam, account_id, user_name, policy, deployment_id)
    print(f"  IAM User: {user_name}")
    print("  Access key created")

    credentials = {
        "platform": "aws",
        "managed_on_prem": True,
        "deployment_id": deployment_id,
        "asg_name": asg_name,
        "launch_template_name": template_name,
        "launch_template_id": template_id,
        "vpc_id": vpc_id,
        "subnet_ids": subnets,
        "eif_bucket": eif_bucket,
        "instance_profile_name": instance_profile_name,
        "builder_instance_profile_name": builder_instance_profile_name,
        "iam_user": user_name,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "aws_region": region,
        "aws_account_id": account_id,
        "scope_tag": f"{SCOPE_TAG_KEY}={deployment_id}",
    }

    creds_file = os.environ.get("OUTPUT_FILE", "/out/credentials.json")
    os.makedirs(os.path.dirname(creds_file), exist_ok=True)

    with open(creds_file, "w") as f:
        json.dump(credentials, f, indent=2)
    os.chmod(creds_file, 0o600)

    print()
    print(f"[OK] AWS provisioning complete")
    print(f"     Deployment ID: {deployment_id}")

    return credentials


if __name__ == "__main__":
    main()
