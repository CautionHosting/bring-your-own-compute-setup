# Managed On-Prem Setup

Create AWS infrastructure and IAM credentials for [Caution](https://caution.co) managed on-premises deployments.

| Step | Description |
|------|-------------|
| [1. Run Setup Script](#1-run-setup-script) | Run Docker to create isolated AWS infrastructure |
| [2. Upload to Platform](#2-upload-to-platform) | Send encrypted credentials to Caution |

## 1. Run Setup Script

Prerequisites:
- Docker
- AWS credentials with admin permissions (used only during setup)

```bash
# Copy the example env file and fill in your credentials
cp .env.example .env

docker build -t caution-provisioner-setup .

docker run --rm \
  --env-file .env \
  -v "$(pwd)/out:/out" \
  caution-provisioner-setup
```

This outputs `credentials.json.gpg` to the `out/` directory.

### Optional: Use Existing VPC

If you want to use an existing VPC instead of creating a new one, uncomment and set `VPC_ID` in your `.env` file:

```
VPC_ID=vpc-xxxxxxxx
```

## 2. Upload to Platform

1. Upload `credentials.json.gpg` to the Caution Platform via CLI or UI
2. Add `managed_on_prem: true` to your `Procfile`
3. Deploy with: `caution init && git push caution main`

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

4. **Launch Template** - Preconfigured for enclave instances:
   - Uses Amazon Linux 2023 AMI
   - Attaches the EC2 instance role
   - Tags instances with deployment ID

5. **Auto Scaling Group** - Manages enclave instances:
   - Starts with 0 instances (Caution scales as needed)
   - Uses the launch template
   - Spans all subnets in the VPC

6. **Scoped IAM User** - Credentials for Caution to manage the deployment:
   - Can only manage resources tagged with the deployment ID
   - Can only access the specific S3 bucket
   - Can only scale the specific ASG
   - Encrypted with Caution's public key before output

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

- Read/write enclave images to the deployment's S3 bucket
- Start/stop/terminate EC2 instances with the deployment tag
- Create new instances via the ASG (automatically tagged)
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

To remove all resources created by setup, set `DEPLOYMENT_ID` in your `.env` file (you can find this in the `credentials.json` from the original setup):

```
DEPLOYMENT_ID=your-deployment-id
```

Then run:

```bash
docker run --rm \
  --env-file .env \
  caution-provisioner-setup python teardown.py
```

This will delete:
- Auto Scaling Group
- Launch Template
- IAM User and Policy
- EC2 Instance Role and Profile
- S3 Bucket (emptied first)
- VPC and related resources (if created by setup)
