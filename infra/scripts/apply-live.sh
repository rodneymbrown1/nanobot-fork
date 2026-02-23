#!/usr/bin/env bash
# apply-live.sh — Apply the live branch state to the running instance.
#
# Called by GitHub Actions after a PR merge into `live`, or manually.
# Steps:
#   1. Pull latest live branch
#   2. Merge MCP server declarations from stack-manifest.json into config.json
#   3. Restart nanobot service
#
# Assumes workspace is a git repo checked out to the live branch.

set -euo pipefail
exec >> /var/log/nanobot-apply.log 2>&1
echo "=== apply-live started at $(date) ==="

WORKSPACE="/data/.nanobot/workspace"
CONFIG="/data/.nanobot/config.json"
MANIFEST="$WORKSPACE/stack-manifest.json"

# ── 1. Pull latest live branch ──────────────────────────────────────────────
cd "$WORKSPACE"
git fetch origin live
git reset --hard origin/live
echo "Checked out latest live branch"

# ── 2. Merge MCP servers from manifest into config.json ─────────────────────
if [ -f "$MANIFEST" ] && [ -f "$CONFIG" ] && command -v jq &>/dev/null; then
  # Build MCP servers object from manifest array.
  # Preserves existing env values in config.json — only adds new server
  # declarations. Never overwrites secrets already present.
  MCP_FROM_MANIFEST=$(jq -r '
    .mcp_servers // [] | map({
      key: .name,
      value: (
        {command: .command, args: .args}
        + if .url != "" and .url != null then {url: .url} else {} end
      )
    }) | from_entries
  ' "$MANIFEST")

  if [ "$MCP_FROM_MANIFEST" != "{}" ] && [ "$MCP_FROM_MANIFEST" != "null" ]; then
    # Merge: manifest servers are added, existing config servers preserved.
    # Existing env values in config.json are NOT overwritten.
    UPDATED=$(jq --argjson manifest_mcp "$MCP_FROM_MANIFEST" '
      .tools.mcp_servers = (
        (.tools.mcp_servers // {}) * $manifest_mcp
      )
    ' "$CONFIG")
    echo "$UPDATED" > "$CONFIG"
    chmod 600 "$CONFIG"
    echo "Merged MCP servers from manifest into config.json"
  else
    echo "No MCP servers in manifest, skipping config merge"
  fi
else
  echo "Skipping config merge (missing manifest, config, or jq)"
fi

# ── 3. Restart nanobot ──────────────────────────────────────────────────────
echo "Restarting nanobot service..."
systemctl restart nanobot
echo "=== apply-live completed at $(date) ==="
