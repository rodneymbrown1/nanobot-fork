#!/bin/bash
# Nanobot instance bootstrap (Lightsail user data).
# CF substitutions: ${AWSAccessKeyId}, ${AWSSecretKey}, ${SecretArn},
#   ${AWS::AccountId}, ${AWS::Region}

[ -z "$NANOBOT_IN_BASH" ] && exec env NANOBOT_IN_BASH=1 /bin/bash "$0" "$@"

set -euo pipefail
exec > /var/log/nanobot-setup.log 2>&1

echo "=== Nanobot bootstrap $(date) ==="

AWS_ACCESS_KEY_ID="${AWSAccessKeyId}"
AWS_SECRET_ACCESS_KEY="${AWSSecretKey}"
AWS_DEFAULT_REGION="${AWS::Region}"
SECRET_ARN="${SecretArn}"
ECR_REPO_URI="${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/nanobot"
ECR_REGISTRY="${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com"

export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION

echo "--- [1/10] System packages ---"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
apt-get install -y curl ca-certificates gnupg unzip nginx certbot python3-certbot-nginx

echo "--- [2/10] Docker ---"
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

echo "--- [3/10] AWS CLI ---"
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp/
/tmp/aws/install
rm -rf /tmp/aws /tmp/awscliv2.zip

echo "--- [4/10] AWS credentials ---"
mkdir -p /root/.aws
cat > /root/.aws/credentials << EOF
[default]
aws_access_key_id = $AWS_ACCESS_KEY_ID
aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
EOF
cat > /root/.aws/config << EOF
[default]
region = $AWS_DEFAULT_REGION
output = json
EOF
chmod 600 /root/.aws/credentials /root/.aws/config

echo "--- [5/10] Data disk ---"

DISK_DEVICE=""
for attempt in $(seq 1 12); do
  for dev in /dev/xvdf /dev/nvme1n1 /dev/sdf; do
    if [ -b "$dev" ]; then
      DISK_DEVICE=$dev
      break 2
    fi
  done
  echo "  Waiting for disk (attempt $attempt/12)..."
  sleep 5
done

if [ -n "$DISK_DEVICE" ]; then
  echo "  Found disk: $DISK_DEVICE"
  if ! blkid "$DISK_DEVICE" > /dev/null 2>&1; then
    echo "  Formatting $DISK_DEVICE (first boot)..."
    mkfs.ext4 -F "$DISK_DEVICE"
  fi
  mkdir -p /data
  mount "$DISK_DEVICE" /data || true
  DISK_UUID=$(blkid -s UUID -o value "$DISK_DEVICE")
  grep -q "$DISK_UUID" /etc/fstab || \
    echo "UUID=$DISK_UUID /data ext4 defaults,nofail 0 2" >> /etc/fstab
  echo "  Disk mounted at /data (UUID: $DISK_UUID)"
else
  echo "  WARNING: No extra disk found. Using /data on root volume (not persistent across terminate)."
  mkdir -p /data
fi

mkdir -p /data/.nanobot /data/.nanobot/sessions /data/.nanobot/memory /data/.nanobot/workspace
ln -sfn /data/.nanobot /root/.nanobot
chmod 700 /data/.nanobot

echo "--- [6/10] docker-compose.yml ---"
mkdir -p /opt/nanobot

cat > /opt/nanobot/docker-compose.yml << EOF
services:
  nanobot-gateway:
    image: $ECR_REPO_URI:latest
    container_name: nanobot-gateway
    command: ["gateway"]
    restart: unless-stopped
    env_file:
      - .env.nanobot
    volumes:
      - /data/.nanobot/sessions:/root/.nanobot/sessions
      - /data/.nanobot/memory:/root/.nanobot/memory
      - /data/.nanobot/workspace:/root/.nanobot/workspace
    environment:
      - NANOBOT_GATEWAY__HOST=127.0.0.1
      - NANOBOT_GATEWAY__PORT=18790
    deploy:
      resources:
        limits:
          cpus: "1"
          memory: 1G
        reservations:
          cpus: "0.25"
          memory: 256M
EOF

echo "--- [7/10] start.sh ---"

cat > /opt/nanobot/start.sh << 'STARTSCRIPT'
#!/bin/bash
set -euo pipefail
exec >> /var/log/nanobot-start.log 2>&1
echo "=== nanobot start at $(date) ==="

ENV_FILE="/opt/nanobot/.env.nanobot"
COMPOSE_DIR="/opt/nanobot"
SECRET_ARN="__SECRET_ARN__"
ECR_REGISTRY="__ECR_REGISTRY__"

# Read config from Secrets Manager
echo "Reading config from Secrets Manager..."
SECRET_VALUE=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ARN" \
  --query SecretString \
  --output text 2>/dev/null || true)

if [ -z "$SECRET_VALUE" ] || echo "$SECRET_VALUE" | grep -q "REPLACE_ME"; then
  echo ""
  echo "================================================================"
  echo "  Config not yet populated. Run scripts/put-secret.sh first."
  echo "  Secret ARN: $SECRET_ARN"
  echo "================================================================"
  echo ""
  exit 1
fi

# Convert JSON config → NANOBOT_* env vars and write to env file (0600 perms).
# No plaintext config.json on disk.
echo "Converting config to env vars..."
python3 -c "
import json, sys

def flatten(obj, prefix='NANOBOT'):
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f'{prefix}__{k.upper()}'
            items.extend(flatten(v, new_key))
    elif isinstance(obj, list):
        items.append((prefix, json.dumps(obj)))
    else:
        items.append((prefix, str(v if (v := obj) is not None else '')))
    return items

data = json.loads(sys.stdin.read())
with open('$ENV_FILE', 'w') as f:
    for key, val in flatten(data):
        # Escape newlines and quotes for docker env file format
        val = val.replace('\\\\', '\\\\\\\\').replace('\"', '\\\\\"').replace('\\n', '\\\\n')
        f.write(f'{key}={val}\n')
" <<< "$SECRET_VALUE"
chmod 600 "$ENV_FILE"
echo "✓ Env file written to $ENV_FILE"

# Authenticate with ECR and pull the latest image
echo "Authenticating with ECR..."
aws ecr get-login-password | \
  docker login --username AWS --password-stdin "$ECR_REGISTRY" 2>/dev/null

cd "$COMPOSE_DIR"
echo "Pulling latest image..."
docker compose pull --quiet || true

echo "Starting nanobot gateway..."
docker compose up -d
echo "✓ Gateway started successfully."
STARTSCRIPT

# Inject CF values
sed -i "s|__SECRET_ARN__|$SECRET_ARN|g" /opt/nanobot/start.sh
sed -i "s|__ECR_REGISTRY__|$ECR_REGISTRY|g" /opt/nanobot/start.sh
chmod +x /opt/nanobot/start.sh

echo "--- [8/10] systemd ---"

cat > /etc/systemd/system/nanobot.service << 'SERVICE'
[Unit]
Description=Nanobot AI Gateway
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/nanobot/start.sh
ExecStop=/usr/bin/docker compose -f /opt/nanobot/docker-compose.yml down
WorkingDirectory=/opt/nanobot
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable nanobot

echo "--- [9/10] nginx ---"

cat > /etc/nginx/sites-available/nanobot << 'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location / {
        proxy_pass         http://127.0.0.1:18790;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/nanobot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
systemctl enable nginx

echo "--- [10/10] git credentials ---"

cat > /opt/nanobot/setup-git-credentials.sh << 'GITSCRIPT'
#!/usr/bin/env bash
set -euo pipefail
SECRET_NAME="${1:-nanobot/deploy-key}"
SSH_DIR="/root/.ssh"
KEY_FILE="$SSH_DIR/nanobot_deploy_key"
DEPLOY_KEY=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_NAME" \
  --query SecretString \
  --output text 2>/dev/null || true)
if [ -z "$DEPLOY_KEY" ]; then
  echo "Deploy key not found ($SECRET_NAME). Git push to live disabled until configured."
  exit 0
fi
mkdir -p "$SSH_DIR"
echo "$DEPLOY_KEY" > "$KEY_FILE"
chmod 600 "$KEY_FILE"
cat > "$SSH_DIR/config" << 'SSHEOF'
Host github.com
  IdentityFile /root/.ssh/nanobot_deploy_key
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
SSHEOF
chmod 600 "$SSH_DIR/config"
git config --global user.name "nanobot[bot]"
git config --global user.email "nanobot[bot]@users.noreply.github.com"
echo "Git credentials configured."
GITSCRIPT
chmod +x /opt/nanobot/setup-git-credentials.sh
/opt/nanobot/setup-git-credentials.sh || true

cat > /opt/nanobot/apply-live.sh << 'APPLYSCRIPT'
#!/usr/bin/env bash
set -euo pipefail
exec >> /var/log/nanobot-apply.log 2>&1
echo "=== apply-live started at $(date) ==="
WORKSPACE="/data/.nanobot/workspace"
ENV_FILE="/opt/nanobot/.env.nanobot"
MANIFEST="$WORKSPACE/stack-manifest.json"
SECRET_ARN="__SECRET_ARN__"

cd "$WORKSPACE"
git fetch origin live
git reset --hard origin/live
echo "Checked out latest live branch"

echo "Refreshing secrets..."
SECRET_VALUE=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ARN" \
  --query SecretString \
  --output text 2>/dev/null || true)

if [ -n "$SECRET_VALUE" ] && ! echo "$SECRET_VALUE" | grep -q "REPLACE_ME"; then
  python3 -c "
import json, sys

def flatten(obj, prefix='NANOBOT'):
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f'{prefix}__{k.upper()}'
            items.extend(flatten(v, new_key))
    elif isinstance(obj, list):
        items.append((prefix, json.dumps(obj)))
    else:
        items.append((prefix, str(v if (v := obj) is not None else '')))
    return items

data = json.loads(sys.stdin.read())
with open('$ENV_FILE', 'w') as f:
    for key, val in flatten(data):
        val = val.replace('\\\\', '\\\\\\\\').replace('\"', '\\\\\"').replace('\\n', '\\\\n')
        f.write(f'{key}={val}\n')
" <<< "$SECRET_VALUE"
  chmod 600 "$ENV_FILE"
  echo "✓ Env file refreshed from Secrets Manager"
else
  echo "WARNING: Could not read secret, keeping existing env file"
fi

if [ -f "$MANIFEST" ] && [ -f "$ENV_FILE" ] && command -v jq &>/dev/null; then
  MCP_JSON=$(jq -c '.mcp_servers // [] | map({key: .name, value: ({command: .command, args: .args} + if .url != "" and .url != null then {url: .url} else {} end)}) | from_entries' "$MANIFEST")
  if [ "$MCP_JSON" != "{}" ] && [ "$MCP_JSON" != "null" ]; then
    # Strip old MCP server vars and append new value
    grep -v '^NANOBOT__TOOLS__MCP_SERVERS=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
    echo "NANOBOT__TOOLS__MCP_SERVERS=$MCP_JSON" >> "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Patched MCP servers into env file"
  fi
fi

echo "Restarting nanobot..."
systemctl restart nanobot

echo "Waiting for container health..."
for i in $(seq 1 15); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' nanobot-gateway 2>/dev/null || echo "unknown")
  if [ "$STATUS" = "healthy" ]; then
    echo "✓ Container healthy after ${i} checks"
    break
  fi
  if [ "$i" -eq 15 ]; then
    echo "WARNING: Container not healthy after 15 checks (status: $STATUS)"
  fi
  sleep 2
done
echo "=== apply-live completed at $(date) ==="
APPLYSCRIPT
sed -i "s|__SECRET_ARN__|$SECRET_ARN|g" /opt/nanobot/apply-live.sh
chmod +x /opt/nanobot/apply-live.sh

/opt/nanobot/start.sh || true
echo "=== Bootstrap complete $(date) ==="
