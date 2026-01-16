#!/bin/sh
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="linux/amd64"
OCI_OUTPUT="$DIR/build/oci"
IMAGE_NAME="caution-managed-on-prem-aws-provisioner"

export DOCKER_BUILDKIT=1
export SOURCE_DATE_EPOCH=1

mkdir -p "$OCI_OUTPUT"

echo "Building deterministic OCI image..."
docker build -f "$DIR/Dockerfile" "$DIR" \
	--platform "$PLATFORM" \
	--output type=oci,rewrite-timestamp=true,force-compression=true,dest="$OCI_OUTPUT/$IMAGE_NAME.tar",name="$IMAGE_NAME" \
	"$@"

echo "OCI image saved to: $OCI_OUTPUT/$IMAGE_NAME.tar"
echo ""
echo "To load into docker:"
echo "  docker load < $OCI_OUTPUT/$IMAGE_NAME.tar"
