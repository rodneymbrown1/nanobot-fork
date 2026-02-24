---
name: reconciler
description: Auto-commit declarative stack state to the `live` branch after installing skills, MCP servers, or cron jobs. Use after any operation that changes the running stack (skill install, MCP server config, cron job creation) to keep the live branch in sync with the instance.
---

# Reconciler

After installing or removing any skill, MCP server, or cron job, reconcile the `live` branch so it reflects the current running state.

## Workflow

### 1. Bootstrap workspace repo (idempotent)

```bash
bash ~/.nanobot/workspace/skills/reconciler/scripts/ensure-workspace-repo.sh
```

If the workspace isn't a git repo yet, this initializes it on an orphan `live` branch with `.gitignore` and an empty `stack-manifest.json`. Safe to run every time.

### 2. Update `stack-manifest.json`

Read `references/manifest-schema.md` for the full schema. Update the relevant array (`skills`, `mcp_servers`, or `cron_jobs`) with the change.

### 3. Regenerate `.env.template`

```bash
bash ~/.nanobot/workspace/skills/reconciler/scripts/gen-env-template.sh
```

### 4. Check for uncommitted changes

Before staging, check if there are unexpected uncommitted changes:

```bash
cd ~/.nanobot/workspace && git status --short
```

If there are changes outside `stack-manifest.json`, `.env.template`, and `skills/`, warn the user and ask before proceeding.

### 5. Stage, commit, and push

```bash
cd ~/.nanobot/workspace
git add stack-manifest.json .env.template skills/
git commit -m "reconcile: <short description of what changed>"
```

### 6. Push with error handling

```bash
cd ~/.nanobot/workspace
git pull --rebase origin live 2>&1
PUSH_OUTPUT=$(git push origin live 2>&1)
PUSH_EXIT=$?
```

If push fails:
- **Auth error**: Tell the user: "Git push failed — deploy key may not be configured. Run `setup-git-credentials.sh` or add a deploy key to the repo."
- **Conflict after rebase**: `git rebase --abort`, then tell the user what happened.
- **Network error**: Tell the user: "Push failed due to network error. Changes are committed locally. Retry with `cd ~/.nanobot/workspace && git push origin live`."

**Always report the outcome** — either "Reconciled and pushed to live" or a clear error with next steps. Never silently swallow a push failure.

## Rules

- **Commit prefix**: Always use `reconcile:` prefix. This prevents circular deploys (GitHub Actions skips deploy for reconcile commits).
- **Never commit secrets**: No API keys, tokens, or passwords. The manifest tracks `env_keys` (names only). `.env.template` uses `<REQUIRED_SECRET>` placeholders.
- **Never commit config.json**: It contains secrets and is in `.gitignore`.
- **Only touch declared files**: `stack-manifest.json`, `.env.template`, and files under `skills/`. Do not commit `memory/`, `data/`, logs, or session state.

## When to Update the Manifest

| Event | Action |
|-------|--------|
| Skill installed | Add entry to `skills` array |
| Skill removed | Remove entry from `skills` array |
| MCP server added to config | Add entry to `mcp_servers` array |
| MCP server removed | Remove entry from `mcp_servers` array |
| Cron job created | Add entry to `cron_jobs` array |
| Cron job removed | Remove entry from `cron_jobs` array |

## Reference Files

- **Manifest schema**: `references/manifest-schema.md` — full schema and field descriptions for `stack-manifest.json`
