# Managed On-Prem Setup

Create AWS infrastructure and IAM credentials for [Caution](https://caution.co) managed on-premises deployments.

Before running anything, we encourage you to review the code in this repository to understand exactly what resources will be created in your AWS account. The [How It Works](#how-it-works) and [Security Model](#security-model) sections below also explain the infrastructure and permissions in detail.

## Requirements

* Docker
* [Caution CLI](https://codeberg.org/caution/cli) (for automated setup)
* AWS credentials configured

## Step 1: Configure AWS Credentials

You can use either admin credentials or a least-privilege role. We recommend the least-privilege approach.

### Option A: Least-Privilege Role (Recommended)

Create an IAM role with only the permissions needed to run the provisioner:

1. Go to IAM → Policies → Create Policy
2. Select JSON and paste the contents of [`provisioner-role-policy.json`](./provisioner-role-policy.json)
3. Name it `CautionProvisionerPolicy`
4. Go to IAM → Roles → Create Role
5. Select "AWS account" as trusted entity (your own account)
6. Attach `CautionProvisionerPolicy`
7. Name it `CautionProvisionerRole`

Then add a profile to `~/.aws/config` that assumes the role:

```ini
[profile caution-provisioner]
role_arn = arn:aws:iam::YOUR_ACCOUNT_ID:role/CautionProvisionerRole
source_profile = default
region = us-west-2
```

Replace `YOUR_ACCOUNT_ID` with your AWS account ID and `default` with the profile that has permission to assume the role.

### Option B: Admin Credentials

Use your existing AWS credentials with admin permissions. Faster to set up but grants more access than necessary.

## Step 2: Run the Provisioner

Choose one of the following methods. Both respect the `AWS_PROFILE` environment variable if you configured a least-privilege role above.

### Option A: Caution CLI (Recommended)

```bash
caution init --managed-on-prem
```

If you configured a least-privilege role, set the profile first:

```bash
AWS_PROFILE=caution-provisioner caution init --managed-on-prem
```

This will:
1. Detect your AWS credentials
2. Provision all required AWS infrastructure
3. Create your app on Caution
4. Register the credentials automatically

### Option B: Manual Docker Setup

If you prefer to run the provisioner manually or need more control:

```bash
cp .env.example .env
# Edit .env with your AWS credentials

docker build -t caution-provisioner-setup .

docker run --rm \
  --env-file .env \
  -v "$(pwd)/out:/out" \
  caution-provisioner-setup
```

This outputs `credentials.json.gpg` to the `out/` directory. The decrypted credentials payload includes `builder_instance_profile_name` for workloads that need to publish new image objects into the deployment bucket, and the scoped IAM credentials can assign that builder instance profile to EC2 builder instances. Then initialize the app with the encrypted credentials:

```bash
caution init --managed-on-prem --config out/credentials.json.gpg
```

To use an existing VPC instead of creating a new one, set `VPC_ID=vpc-xxxxxxxx` in your `.env` file.

---

## How It Works

The setup script creates an isolated AWS environment for running Caution enclaves:

1. **VPC** - A dedicated /16 VPC with:
   - Public subnets across multiple availability zones
   - Internet gateway with routing
   - DNS hostname support enabled

2. **S3 Bucket** - For storing enclave images:
   - Named `caution-<deployment-id>-images`
   - Public access blocked
   - Tagged with deployment ID

3. **EC2 Instance Role** - Allows enclave instances to:
   - Read enclave images from the S3 bucket
   - Attached via instance profile to launched instances

4. **Builder Role** - Allows builder instances to:
   - Read existing enclave images from the S3 bucket
   - Upload new enclave image objects into the same bucket
   - Use the generated `builder_instance_profile_name` output value when launching EC2 builder instances

5. **Launch Template** - Preconfigured for enclave instances:
   - Uses Amazon Linux 2023 AMI
   - Attaches the EC2 instance role
   - Tags instances with deployment ID

6. **Auto Scaling Group** - Manages enclave instances:
   - Starts with 0 instances (Caution scales as needed)
   - Uses the launch template
   - Spans all subnets in the VPC

7. **Scoped IAM User** - Credentials for Caution to manage the deployment:
   - Can only manage resources tagged with the deployment ID
   - Can only access and tag objects in the specific S3 bucket
   - Can only scale the specific ASG

## Security Model

### Tag-Based Resource Scoping

All resources are tagged with:
```
caution:deployment-id = <deployment-id>
```

The IAM policy uses AWS condition keys to enforce scope:
- `aws:ResourceTag/caution:deployment-id` - For existing resources
- `aws:RequestTag/caution:deployment-id` - For new resources

### What the Scoped Credentials CAN Do

- Read/write and tag enclave image objects in the deployment's S3 bucket
- Start/stop/terminate EC2 instances with the deployment tag
- Create new instances via the ASG (automatically tagged)
- Assign the deployment's EC2 and builder instance profiles to EC2 instances
- Manage volumes, security groups, and EIPs with the deployment tag
- Scale the specific Auto Scaling Group
- Create and manage launch template versions

### What the Scoped Credentials CANNOT Do

- Access any resources without the deployment tag
- Access other S3 buckets
- Modify other Auto Scaling Groups or launch templates
- Access resources in other deployments
- Escalate privileges or modify IAM policies
- Access network resources outside the deployment VPC

### Credential Encryption

When using the manual Docker setup, credentials are GPG-encrypted to Caution's public key before being written to disk. The `.gpg` file can only be decrypted by Caution, so credentials remain protected even if the file is intercepted. You can verify the public key at [`caution-public-key.asc`](./caution-public-key.asc).

When using the CLI, credentials are encrypted and transmitted over TLS without being written to disk.

## Updating IAM Policy

To update the IAM policy for an existing deployment (e.g., after script improvements), set `DEPLOYMENT_ID` and `VPC_ID` in your `.env` file:

```
DEPLOYMENT_ID=your-deployment-id
VPC_ID=vpc-xxxxxxxx
```

Then run:

```bash
docker run --rm \
  --env-file .env \
  caution-provisioner-setup python setup.py --update-policy
```

## Teardown

**WARNING**: This will permanently delete all resources and break Caution's ability to manage this deployment.

### Option A: Caution CLI

If you set up using the CLI, run teardown from the app directory:

```bash
caution teardown --managed-on-prem
```

### Option B: Manual Docker Teardown

```bash
docker run --rm \
  --env-file .env \
  -e TEARDOWN=true -e DEPLOYMENT_ID=<id> \
  caution-provisioner-setup python teardown.py
```

This will delete:
- Auto Scaling Group
- Launch Template
- IAM User and Policy
- EC2 Instance Role and Profile
- Builder Role and Profile
- S3 Bucket (emptied first)
- VPC and related resources (if created by setup)
