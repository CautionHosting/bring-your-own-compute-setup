#!/bin/bash
set -e

echo "=============================================="
echo "Caution Managed On-Prem Setup"
echo "=============================================="
echo ""

if [ -z "$AWS_ACCESS_KEY_ID" ] || [ -z "$AWS_SECRET_ACCESS_KEY" ]; then
    echo "[ERROR] AWS credentials required."
    echo ""
    echo "Required environment variables:"
    echo "  AWS_ACCESS_KEY_ID      - AWS access key with admin permissions"
    echo "  AWS_SECRET_ACCESS_KEY  - AWS secret key"
    echo ""
    echo "Optional environment variables:"
    echo "  AWS_REGION             - AWS region (default: us-west-2)"
    echo "  DEPLOYMENT_ID          - Custom deployment ID (auto-generated if not set)"
    echo "  VPC_ID                 - Specific VPC to use (uses default VPC if not set)"
    echo ""
    exit 1
fi

gpg --import /app/caution-public-key.asc 2>/dev/null
GPG_RECIPIENT=$(gpg --show-keys --with-colons /app/caution-public-key.asc 2>/dev/null | grep '^pub' | cut -d: -f5)

if [ -z "$GPG_RECIPIENT" ]; then
    echo "[ERROR] Could not load Caution public key."
    exit 1
fi

echo "[OK] Credentials will be encrypted to Caution's public key"
echo "     Key ID: $GPG_RECIPIENT"
echo ""

OUTPUT_DIR="${OUTPUT_DIR:-/out}"
mkdir -p "$OUTPUT_DIR"

export OUTPUT_FILE="$OUTPUT_DIR/credentials.json"
python3 /app/setup.py

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "[ERROR] Setup failed - no credentials file generated"
    exit 1
fi

echo ""
echo "[INFO] Encrypting credentials..."

gpg --trust-model always --batch --yes \
    --recipient "$GPG_RECIPIENT" \
    --armor \
    --output "$OUTPUT_DIR/credentials.json.gpg" \
    --encrypt "$OUTPUT_FILE"

rm -f "$OUTPUT_FILE"

echo "[OK] Encrypted credentials: $OUTPUT_DIR/credentials.json.gpg"
echo ""
echo "=============================================="
echo "Setup complete!"
echo "=============================================="
echo ""
echo "Send the file 'credentials.json.gpg' to Caution."
echo "Only Caution can decrypt these credentials."
