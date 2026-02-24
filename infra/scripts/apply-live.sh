#!/usr/bin/env bash
# apply-live.sh — Apply the live branch state to the running instance.
#
# Called by GitHub Actions after a PR merge into `live`, or manually.
# Steps:
#   1. Pull latest live branch
#   2. Re-read secrets from Secrets Manager (picks up new MCP keys, etc.)
#   3. Merge MCP server declarations from stack-manifest.json into env file
#   4. Restart nanobot service and wait for health check
#
# SECRET_ARN is baked in by user-data.sh at bootstrap time.
# Assumes workspace is a git repo checked out to the live branch.

set -euo pipefail
exec >> /var/log/nanobot-apply.log 2>&1
echo "=== apply-live started at $(date) ==="

WORKSPACE="/data/.nanobot/workspace"
ENV_FILE="/opt/nanobot/.env.nanobot"
MANIFEST="$WORKSPACE/stack-manifest.json"
SECRET_ARN="${SECRET_ARN:-__SECRET_ARN__}"

# ── 1. Pull latest live branch ──────────────────────────────────────────────
cd "$WORKSPACE"
git fetch origin live
git reset --hard origin/live
echo "Checked out latest live branch"

# ── 2. Re-read secrets from Secrets Manager ─────────────────────────────────
# Picks up any new API keys / MCP secrets added since last boot.
echo "Refreshing secrets from Secrets Manager..."
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

# ── 3. Merge MCP servers from manifest into env file ────────────────────────
if [ -f "$MANIFEST" ] && [ -f "$ENV_FILE" ] && command -v jq &>/dev/null; then
  MCP_JSON=$(jq -c '
    .mcp_servers // [] | map({
      key: .name,
      value: (
        {command: .command, args: .args}
        + if .url != "" and .url != null then {url: .url} else {} end
      )
    }) | from_entries
  ' "$MANIFEST")

  if [ "$MCP_JSON" != "{}" ] && [ "$MCP_JSON" != "null" ]; then
    # Strip old MCP server vars and append new value
    grep -v '^NANOBOT__TOOLS__MCP_SERVERS=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
    echo "NANOBOT__TOOLS__MCP_SERVERS=$MCP_JSON" >> "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Patched MCP servers into env file"
  else
    echo "No MCP servers in manifest, skipping env file patch"
  fi
else
  echo "Skipping env file patch (missing manifest, env file, or jq)"
fi

# ── 4. Restart nanobot and wait for health ──────────────────────────────────
echo "Restarting nanobot service..."
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
