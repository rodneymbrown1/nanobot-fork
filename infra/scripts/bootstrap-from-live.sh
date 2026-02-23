#!/usr/bin/env bash
# bootstrap-from-live.sh — Initialize a fresh instance from the live branch.
#
# Clones the live branch into the workspace directory, then runs apply-live.sh
# to merge MCP servers into config.json and restart nanobot.
#
# Prerequisites:
#   - Git credentials installed (setup-git-credentials.sh)
#   - config.json exists with at minimum provider API keys
#   - Secrets filled per .env.template guidance
#
# Usage: ./bootstrap-from-live.sh <github-repo-url>
#   e.g. ./bootstrap-from-live.sh git@github.com:user/nanobot.git

set -euo pipefail

REPO_URL="${1:?Usage: bootstrap-from-live.sh <github-repo-url>}"
WORKSPACE="/data/.nanobot/workspace"
APPLY_SCRIPT="/opt/nanobot/apply-live.sh"

echo "=== Bootstrapping from live branch ==="
echo "Repo: $REPO_URL"
echo "Workspace: $WORKSPACE"

# Clone or reinitialize workspace
if [ -d "$WORKSPACE/.git" ]; then
  echo "Workspace already has a git repo. Fetching live branch..."
  cd "$WORKSPACE"
  git remote set-url origin "$REPO_URL" 2>/dev/null || git remote add origin "$REPO_URL"
  git fetch origin live
  git checkout -B live origin/live
else
  echo "Cloning live branch into workspace..."
  mkdir -p "$(dirname "$WORKSPACE")"
  git clone --branch live --single-branch "$REPO_URL" "$WORKSPACE"
fi

echo "Workspace initialized from live branch."

# Run apply script to merge config and restart
if [ -x "$APPLY_SCRIPT" ]; then
  echo "Running apply-live.sh..."
  "$APPLY_SCRIPT"
else
  echo "Warning: $APPLY_SCRIPT not found or not executable."
  echo "Copy apply-live.sh to /opt/nanobot/ and run it manually."
fi

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Next steps:"
echo "  1. Verify config.json has your API keys (from Secrets Manager)"
echo "  2. Check .env.template for any missing secrets"
echo "  3. Run: systemctl status nanobot"
