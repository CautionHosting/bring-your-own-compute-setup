#!/bin/bash
set -e

# Check for required AWS credentials
if [ -z "$AWS_ACCESS_KEY_ID" ] || [ -z "$AWS_SECRET_ACCESS_KEY" ]; then
    echo "[ERROR] AWS credentials required." >&2
    echo "" >&2
    echo "Required environment variables:" >&2
    echo "  AWS_ACCESS_KEY_ID      - AWS access key with admin permissions" >&2
    echo "  AWS_SECRET_ACCESS_KEY  - AWS secret key" >&2
    echo "" >&2
    echo "Optional environment variables:" >&2
    echo "  AWS_SESSION_TOKEN      - AWS session token (for temporary credentials from assumed roles)" >&2
    echo "  AWS_REGION             - AWS region (default: us-west-2)" >&2
    echo "  DEPLOYMENT_ID          - Custom deployment ID (auto-generated if not set)" >&2
    echo "  TEARDOWN               - Set to 'true' to run teardown instead of setup" >&2
    echo "  CLI_MODE               - Set to 'true' to output JSON to stdout (for CLI)" >&2
    echo "" >&2
    exit 1
fi

# Handle teardown mode
if [ "$TEARDOWN" = "true" ]; then
    if [ -z "$DEPLOYMENT_ID" ]; then
        echo "[ERROR] DEPLOYMENT_ID is required for teardown." >&2
        exit 1
    fi
    echo "[INFO] Running teardown for deployment: $DEPLOYMENT_ID" >&2
    python3 /app/teardown.py
    exit $?
fi

# Setup mode
echo "[INFO] Running setup..." >&2

OUTPUT_DIR="${OUTPUT_DIR:-/out}"
mkdir -p "$OUTPUT_DIR"

export OUTPUT_FILE="$OUTPUT_DIR/credentials.json"

# In CLI mode, redirect setup.py output to stderr so only JSON goes to stdout
if [ "$CLI_MODE" = "true" ]; then
    python3 /app/setup.py >&2
    SETUP_EXIT=$?
    if [ $SETUP_EXIT -ne 0 ]; then
        echo "[ERROR] Setup failed with exit code $SETUP_EXIT" >&2
        exit $SETUP_EXIT
    fi
    if [ ! -f "$OUTPUT_FILE" ]; then
        echo "[ERROR] Setup failed - no credentials file generated" >&2
        exit 1
    fi
    # Output only the JSON to stdout
    cat "$OUTPUT_FILE"
    rm -f "$OUTPUT_FILE"
    exit 0
fi

# Interactive mode
python3 /app/setup.py

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "[ERROR] Setup failed - no credentials file generated" >&2
    exit 1
fi

# Interactive mode: encrypt credentials
echo "[INFO] Loading Caution public key..." >&2
gpg --import /app/caution-public-key.asc 2>/dev/null
GPG_RECIPIENT=$(gpg --show-keys --with-colons /app/caution-public-key.asc 2>/dev/null | grep '^pub' | cut -d: -f5)

if [ -z "$GPG_RECIPIENT" ]; then
    echo "[ERROR] Could not load Caution public key." >&2
    exit 1
fi

echo "[OK] Credentials will be encrypted to Caution's public key" >&2
echo "     Key ID: $GPG_RECIPIENT" >&2
echo "" >&2

echo "[INFO] Encrypting credentials..." >&2

gpg --trust-model always --batch --yes \
    --recipient "$GPG_RECIPIENT" \
    --armor \
    --output "$OUTPUT_DIR/credentials.json.gpg" \
    --encrypt "$OUTPUT_FILE"

rm -f "$OUTPUT_FILE"

echo "[OK] Encrypted credentials: $OUTPUT_DIR/credentials.json.gpg" >&2
echo "" >&2
echo "=============================================="  >&2
echo "Setup complete!" >&2
echo "==============================================" >&2
echo "" >&2
echo "Send the file 'credentials.json.gpg' to Caution." >&2
echo "Only Caution can decrypt these credentials." >&2
