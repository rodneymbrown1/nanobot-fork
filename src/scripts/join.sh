#!/usr/bin/env bash
# Nanobot team member onboarding script.
#
# Usage:
#   curl -sL https://.../join.sh | INVITE_TOKEN=abc123 INVITE_ID=xxx bash
#
# Prerequisites:
#   - AWS CLI credentials (admin will provide a scoped IAM access key)
#   - INVITE_TOKEN and INVITE_ID environment variables

set -euo pipefail

# ── 1. Validate inputs ───────────────────────────────────────────────────
if [ -z "${INVITE_TOKEN:-}" ] || [ -z "${INVITE_ID:-}" ]; then
  echo "ERROR: INVITE_TOKEN and INVITE_ID must be set."
  echo ""
  echo "Usage:"
  echo "  INVITE_TOKEN=<token> INVITE_ID=<id> bash join.sh"
  exit 1
fi

echo "=== Nanobot Join ==="
echo ""

# ── 2. Check/install AWS CLI ─────────────────────────────────────────────
if ! command -v aws &>/dev/null; then
  echo "AWS CLI not found. Installing..."
  if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  Download from: https://awscli.amazonaws.com/AWSCLIV2.pkg"
    echo "  Or: brew install awscli"
    exit 1
  else
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
    unzip -q /tmp/awscliv2.zip -d /tmp/
    sudo /tmp/aws/install
    rm -rf /tmp/aws /tmp/awscliv2.zip
  fi
fi

# ── 3. Check AWS credentials ────────────────────────────────────────────
if ! aws sts get-caller-identity &>/dev/null; then
  echo ""
  echo "AWS credentials not configured. Your admin should have provided"
  echo "an IAM access key scoped to nanobot resources."
  echo ""
  read -rp "AWS Access Key ID: " AWS_ACCESS_KEY_ID
  read -rsp "AWS Secret Access Key: " AWS_SECRET_ACCESS_KEY
  echo ""

  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

  # Persist for this session
  mkdir -p ~/.aws
  cat > ~/.aws/credentials <<EOF
[default]
aws_access_key_id = $AWS_ACCESS_KEY_ID
aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
EOF
  chmod 600 ~/.aws/credentials

  if ! aws sts get-caller-identity &>/dev/null; then
    echo "ERROR: AWS credentials are invalid."
    exit 1
  fi
fi

echo "AWS identity: $(aws sts get-caller-identity --query Arn --output text)"

# ── 4. Read and validate invite ─────────────────────────────────────────
INVITE_SECRET_NAME="nanobot/invites/${INVITE_ID}"
echo ""
echo "Reading invite..."

INVITE_JSON=$(aws secretsmanager get-secret-value \
  --secret-id "$INVITE_SECRET_NAME" \
  --query SecretString \
  --output text 2>/dev/null) || {
  echo "ERROR: Invite not found. Check your INVITE_ID."
  exit 1
}

# Validate token
STORED_TOKEN=$(echo "$INVITE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
if [ "$STORED_TOKEN" != "$INVITE_TOKEN" ]; then
  echo "ERROR: Invalid invite token."
  exit 1
fi

# Check expiry
EXPIRED=$(echo "$INVITE_JSON" | python3 -c "
import json, sys
from datetime import datetime, timezone
data = json.load(sys.stdin)
expires = datetime.fromisoformat(data['expiresAt'])
print('yes' if expires < datetime.now(timezone.utc) else 'no')
")
if [ "$EXPIRED" = "yes" ]; then
  echo "ERROR: This invite has expired."
  exit 1
fi

# Check if already used
USED=$(echo "$INVITE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('used', False))")
if [ "$USED" = "True" ]; then
  echo "ERROR: This invite has already been used."
  exit 1
fi

# Extract invite data
INSTANCE_NAME=$(echo "$INVITE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['instanceName'])")
ORG_SECRET_ARN=$(echo "$INVITE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['orgSecretArn'])")
ECR_REPO_URI=$(echo "$INVITE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['ecrRepoUri'])")
AGENT_BUCKET=$(echo "$INVITE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('agentBucket', ''))")
REGION=$(echo "$INVITE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['region'])")

echo "  Instance:  $INSTANCE_NAME"
echo "  Region:    $REGION"
echo "  ECR:       $ECR_REPO_URI"
echo ""

# ── 5. Collect per-instance secrets ─────────────────────────────────────
echo "--- Configure your instance ---"
echo ""

read -rp "Telegram bot token (leave blank to skip): " TG_TOKEN
if [ -n "$TG_TOKEN" ]; then
  read -rp "Telegram allow-from user IDs (comma-separated): " TG_ALLOW
fi

read -rsp "Gateway API key (Bearer token): " GW_API_KEY
echo ""

# Build instance config JSON
INSTANCE_CONFIG=$(python3 -c "
import json
config = {
    'gateway': {'apiKey': '$GW_API_KEY'},
    'channels': {}
}
tg_token = '$TG_TOKEN'
if tg_token:
    allow_from = [x.strip() for x in '${TG_ALLOW:-}'.split(',') if x.strip()]
    config['channels']['telegram'] = {
        'enabled': True,
        'token': tg_token,
        'allowFrom': allow_from
    }
print(json.dumps(config))
")

# ── 6. Write instance secret ────────────────────────────────────────────
INSTANCE_SECRET_NAME="nanobot/instance/${INSTANCE_NAME}"
echo ""
echo "Writing instance secret: $INSTANCE_SECRET_NAME"

# Try to update existing, or create new
if aws secretsmanager describe-secret --secret-id "$INSTANCE_SECRET_NAME" &>/dev/null; then
  aws secretsmanager put-secret-value \
    --secret-id "$INSTANCE_SECRET_NAME" \
    --secret-string "$INSTANCE_CONFIG"
else
  aws secretsmanager create-secret \
    --name "$INSTANCE_SECRET_NAME" \
    --description "Per-instance config for $INSTANCE_NAME" \
    --secret-string "$INSTANCE_CONFIG"
fi
echo "  Instance secret written."

# ── 7. Seed S3 identity ─────────────────────────────────────────────────
if [ -n "$AGENT_BUCKET" ]; then
  echo ""
  echo "Seeding identity files in s3://$AGENT_BUCKET/$INSTANCE_NAME/ ..."

  # Create starter SOUL.md
  cat > /tmp/SOUL.md <<'SOULEOF'
# Soul

You are a helpful AI assistant.

## Personality
- Professional and friendly
- Concise and clear
- Proactive in offering help

## Guidelines
- Always be honest about your limitations
- Ask clarifying questions when needed
- Provide actionable suggestions
SOULEOF

  # Create starter USER.md
  cat > /tmp/USER.md <<'USEREOF'
# User

## About
- Team member

## Preferences
- Clear, concise communication
USEREOF

  aws s3 cp /tmp/SOUL.md "s3://${AGENT_BUCKET}/${INSTANCE_NAME}/SOUL.md"
  aws s3 cp /tmp/USER.md "s3://${AGENT_BUCKET}/${INSTANCE_NAME}/USER.md"
  rm -f /tmp/SOUL.md /tmp/USER.md
  echo "  Identity files uploaded to S3."
fi

# ── 8. Mark invite as used ──────────────────────────────────────────────
UPDATED_INVITE=$(echo "$INVITE_JSON" | python3 -c "
import json, sys
data = json.load(sys.stdin)
data['used'] = True
print(json.dumps(data))
")
aws secretsmanager put-secret-value \
  --secret-id "$INVITE_SECRET_NAME" \
  --secret-string "$UPDATED_INVITE"
echo ""
echo "Invite marked as used."

# ── 9. Print next steps ─────────────────────────────────────────────────
echo ""
echo "================================================================"
echo "  Onboarding complete!"
echo ""
echo "  Instance: $INSTANCE_NAME"
echo "  Region:   $REGION"
echo ""
echo "  Next steps:"
echo "    1. Ask your admin to deploy the instance stack:"
echo "       nanobot deploy --instance $INSTANCE_NAME"
echo ""
echo "    2. Or if already deployed, SSH in:"
echo "       (admin will share the IP address)"
echo "================================================================"
echo ""
