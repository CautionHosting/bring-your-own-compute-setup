#!/bin/bash
# SPDX-FileCopyrightText: 2025 Caution SEZC
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Caution Managed On-Prem Setup${NC}"
echo "=============================="
echo ""

# Check for AWS CLI
if ! command -v aws &> /dev/null; then
    echo -e "${RED}❌ AWS CLI not found. Please install it first.${NC}"
    exit 1
fi

# Check for Terraform/OpenTofu
if command -v tofu &> /dev/null; then
    TF_CMD="tofu"
elif command -v terraform &> /dev/null; then
    TF_CMD="terraform"
else
    echo -e "${RED}❌ Neither OpenTofu nor Terraform found. Please install one.${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Using: $TF_CMD"

# Check AWS credentials
echo -e "${YELLOW}Checking AWS credentials...${NC}"
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}❌ AWS credentials not configured or invalid.${NC}"
    echo "Please run: aws configure"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo -e "${GREEN}✓${NC} AWS Account: $ACCOUNT_ID"

# Initialize and apply
echo ""
echo -e "${YELLOW}Initializing Terraform...${NC}"
$TF_CMD init

echo ""
echo -e "${YELLOW}Creating execution plan...${NC}"
$TF_CMD plan -out=tfplan

echo ""
read -p "Continue with apply? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

$TF_CMD apply tfplan

# Save outputs
echo ""
echo -e "${YELLOW}Saving credentials...${NC}"
$TF_CMD output -json > outputs.json

ACCESS_KEY=$($TF_CMD output -raw aws_access_key_id)
SECRET_KEY=$($TF_CMD output -raw aws_secret_access_key)
REGION=$($TF_CMD output -raw aws_region)

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Credentials for Caution Platform${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Add these to your Caution dashboard under Cloud Credentials:"
echo ""
echo "  Access Key ID:     $ACCESS_KEY"
echo "  Secret Access Key: $SECRET_KEY"
echo "  Region:            $REGION"
echo ""
echo -e "${GREEN}========================================${NC}"

# Save to file
CREDS_FILE="caution-credentials.env"
cat > $CREDS_FILE << EOF
AWS_ACCESS_KEY_ID=$ACCESS_KEY
AWS_SECRET_ACCESS_KEY=$SECRET_KEY
AWS_REGION=$REGION
EOF

chmod 600 $CREDS_FILE
echo ""
echo -e "${GREEN}✓${NC} Credentials saved to: $CREDS_FILE"
echo -e "${YELLOW}⚠${NC}  Keep this file secure!"
echo ""
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "Next steps:"
echo "1. Go to your Caution dashboard"
echo "2. Navigate to Cloud Credentials"
echo "3. Add the credentials above"
echo "4. Deploy your first app with: caution init && git push caution main"
