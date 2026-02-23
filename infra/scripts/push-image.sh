#!/bin/bash
# =============================================================================
# Build the nanobot Docker image and push it to ECR.
# Run this AFTER `cdk deploy` so the ECR repository exists.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Resolve ECR repo URI from CDK outputs
STACK_NAME="${STACK_NAME:-NanobotStack}"
REGION="${AWS_DEFAULT_REGION:-$(aws configure get region)}"

echo "Fetching ECR repo URI from CloudFormation outputs..."
ECR_REPO_URI=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='EcrRepoUri'].OutputValue" \
  --output text)

if [ -z "$ECR_REPO_URI" ]; then
  echo "ERROR: Could not find EcrRepoUri in stack $STACK_NAME outputs."
  echo "       Make sure you have run 'cdk deploy' first."
  exit 1
fi

ECR_REGISTRY=$(echo "$ECR_REPO_URI" | cut -d'/' -f1)

echo "ECR repo: $ECR_REPO_URI"
echo "Registry: $ECR_REGISTRY"
echo ""

# Authenticate with ECR
echo "Authenticating with ECR..."
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$ECR_REGISTRY"

# Build from the project root Dockerfile
echo "Building Docker image (platform: linux/amd64)..."
docker build \
  --platform linux/amd64 \
  --tag nanobot:latest \
  "$PROJECT_ROOT"

# Tag and push
echo "Pushing image to ECR..."
docker tag nanobot:latest "$ECR_REPO_URI:latest"
docker push "$ECR_REPO_URI:latest"

echo ""
echo "✓ Image pushed: $ECR_REPO_URI:latest"
echo ""
echo "To update the running instance:"
echo "  ssh ubuntu@<IP> 'sudo systemctl restart nanobot'"
