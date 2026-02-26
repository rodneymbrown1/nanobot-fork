#!/usr/bin/env bash
# ensure-workspace-repo.sh — Bootstrap workspace as a git repo on the live branch.
#
# Idempotent: safe to call before every reconcile. Only performs setup
# steps that haven't been done yet.
#
# Usage: ./ensure-workspace-repo.sh [workspace_path] [remote_url]

set -euo pipefail

WORKSPACE="${1:-$HOME/.nanobot/workspace}"
REMOTE_URL="${2:-}"  # Optional: set remote if provided
MANIFEST="$WORKSPACE/stack-manifest.json"

# ── 1. Ensure workspace directory exists ────────────────────────────────────
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# ── 2. Initialize git repo if not already one ──────────────────────────────
if [ ! -d ".git" ]; then
  echo "Initializing git repo in $WORKSPACE..."
  git init
  git checkout --orphan live

  # Configure identity if not already set globally
  if ! git config user.name &>/dev/null; then
    git config user.name "nanobot[bot]"
    git config user.email "nanobot[bot]@users.noreply.github.com"
  fi

  echo "Git repo initialized on orphan 'live' branch."
fi

# ── 3. Ensure we're on the live branch ──────────────────────────────────────
CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "")
if [ "$CURRENT_BRANCH" != "live" ]; then
  # Check if live branch exists
  if git show-ref --verify --quiet refs/heads/live 2>/dev/null; then
    git checkout live
  else
    git checkout --orphan live
  fi
  echo "Switched to live branch."
fi

# ── 4. Set remote if provided and not already set ───────────────────────────
if [ -n "$REMOTE_URL" ]; then
  EXISTING_REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
  if [ -z "$EXISTING_REMOTE" ]; then
    git remote add origin "$REMOTE_URL"
    echo "Remote 'origin' set to $REMOTE_URL"
  elif [ "$EXISTING_REMOTE" != "$REMOTE_URL" ]; then
    git remote set-url origin "$REMOTE_URL"
    echo "Remote 'origin' updated to $REMOTE_URL"
  fi
fi

# ── 5. Ensure .gitignore exists ─────────────────────────────────────────────
if [ ! -f ".gitignore" ]; then
  cat > .gitignore << 'GITIGNORE'
config.json
memory/
data/
sessions/
*.log
*.key
*.pem
.env
__pycache__/
GITIGNORE
  echo "Created .gitignore"
fi

# ── 6. Ensure stack-manifest.json exists ────────────────────────────────────
if [ ! -f "$MANIFEST" ]; then
  cat > "$MANIFEST" << 'MANIFEST_JSON'
{
  "version": 1,
  "skills": [],
  "mcp_servers": [],
  "cron_jobs": [],
  "env_shape": {}
}
MANIFEST_JSON
  echo "Created stack-manifest.json"
fi

# ── 7. Ensure skills directory exists ───────────────────────────────────────
mkdir -p skills

# ── 8. Create initial commit if repo has no commits ─────────────────────────
if ! git rev-parse HEAD &>/dev/null 2>&1; then
  git add .gitignore stack-manifest.json
  git commit -m "reconcile: initialize live branch with empty manifest"
  echo "Created initial commit."

  # Push if remote is configured
  if git remote get-url origin &>/dev/null 2>&1; then
    git push -u origin live 2>/dev/null && echo "Pushed initial commit to origin/live." || \
      echo "Warning: Could not push to origin. Push manually or check credentials."
  fi
fi

echo "Workspace repo ready at $WORKSPACE (branch: live)"
