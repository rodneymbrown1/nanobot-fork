#!/usr/bin/env bash
# gen-env-template.sh — Generate .env.template from config.json
#
# Reads ~/.nanobot/config.json, flattens all keys to NANOBOT_*__* format
# (matching Pydantic env_prefix="NANOBOT_" and env_nested_delimiter="__"),
# replaces secret-like values with <REQUIRED_SECRET>, others with <VALUE>.
#
# Also updates the env_shape section in stack-manifest.json.
#
# Usage: ./gen-env-template.sh [config_path] [workspace_path]

set -euo pipefail

CONFIG_PATH="${1:-$HOME/.nanobot/config.json}"
WORKSPACE="${2:-$HOME/.nanobot/workspace}"
ENV_TEMPLATE="$WORKSPACE/.env.template"
MANIFEST="$WORKSPACE/stack-manifest.json"

if [ ! -f "$CONFIG_PATH" ]; then
  echo "Error: config.json not found at $CONFIG_PATH" >&2
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required but not installed" >&2
  exit 1
fi

# Flatten JSON to NANOBOT_*__* env var paths, classify as secret or value
ENV_SHAPE=$(jq -r '
  # Recursive flattening with path tracking
  [paths(scalars) as $p | {
    key: (["NANOBOT"] + [$p[] | tostring | ascii_upcase]) | join("__"),
    value: getpath($p)
  }]
  | map({
      key: .key,
      placeholder: (
        if (.key | test("KEY|TOKEN|SECRET|PASSWORD"; "i"))
        then "<REQUIRED_SECRET>"
        else "<VALUE>"
        end
      )
    })
  | from_entries
' "$CONFIG_PATH")

# Write .env.template
echo "# Auto-generated env template — do NOT put real secrets here." > "$ENV_TEMPLATE"
echo "# Fill values and rename to .env, or set as real environment variables." >> "$ENV_TEMPLATE"
echo "#" >> "$ENV_TEMPLATE"
echo "# Generated at: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$ENV_TEMPLATE"
echo "" >> "$ENV_TEMPLATE"

echo "$ENV_SHAPE" | jq -r 'to_entries[] | "\(.key)=\(.value)"' >> "$ENV_TEMPLATE"

echo "Generated $ENV_TEMPLATE"

# Update env_shape in stack-manifest.json (create if missing)
if [ ! -f "$MANIFEST" ]; then
  cat > "$MANIFEST" << 'INIT_MANIFEST'
{
  "version": 1,
  "skills": [],
  "mcp_servers": [],
  "cron_jobs": [],
  "env_shape": {}
}
INIT_MANIFEST
  echo "Created $MANIFEST"
fi

UPDATED=$(jq --argjson shape "$ENV_SHAPE" '.env_shape = $shape' "$MANIFEST")
echo "$UPDATED" > "$MANIFEST"
echo "Updated env_shape in $MANIFEST"
