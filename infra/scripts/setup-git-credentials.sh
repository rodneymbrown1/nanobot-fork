#!/usr/bin/env bash
# setup-git-credentials.sh — Install GitHub deploy key from Secrets Manager.
#
# Pulls the deploy key from AWS Secrets Manager, writes it to /root/.ssh/,
# and configures git identity as nanobot[bot].
#
# The deploy key is scoped to a single repo with write access, allowing
# nanobot to push reconcile commits to the live branch.
#
# Usage: ./setup-git-credentials.sh <secret-name>
#   e.g. ./setup-git-credentials.sh nanobot/deploy-key

set -euo pipefail

SECRET_NAME="${1:-nanobot/deploy-key}"
SSH_DIR="/root/.ssh"
KEY_FILE="$SSH_DIR/nanobot_deploy_key"

echo "=== Setting up git credentials ==="

# Fetch deploy key from Secrets Manager
echo "Fetching deploy key from Secrets Manager ($SECRET_NAME)..."
DEPLOY_KEY=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_NAME" \
  --query SecretString \
  --output text 2>/dev/null || true)

if [ -z "$DEPLOY_KEY" ]; then
  echo "Warning: Deploy key not found in Secrets Manager ($SECRET_NAME)."
  echo "Git push to live branch will not work until the key is configured."
  echo "To set it up:"
  echo "  1. Generate: ssh-keygen -t ed25519 -f nanobot_deploy_key -N ''"
  echo "  2. Add public key to GitHub repo -> Settings -> Deploy keys (write access)"
  echo "  3. Store private key: aws secretsmanager create-secret --name $SECRET_NAME --secret-string file://nanobot_deploy_key"
  exit 0
fi

# Install the key
mkdir -p "$SSH_DIR"
echo "$DEPLOY_KEY" > "$KEY_FILE"
chmod 600 "$KEY_FILE"

# Configure SSH to use this key for github.com
cat > "$SSH_DIR/config" << 'EOF'
Host github.com
  IdentityFile /root/.ssh/nanobot_deploy_key
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
chmod 600 "$SSH_DIR/config"

# Configure git identity
git config --global user.name "nanobot[bot]"
git config --global user.email "nanobot[bot]@users.noreply.github.com"

echo "Git credentials configured successfully."
echo "  Key: $KEY_FILE"
echo "  Identity: nanobot[bot]"
